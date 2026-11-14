"""
Portfolio 3: concurrency anomalies and the mechanisms that control them.

Each scenario opens two real PostgreSQL sessions (A and B) and interleaves
their statements in a fixed order, printing a timeline. When a statement is
expected to block, it is started on a background thread; a third
"monitor" session then shows who is waiting for whom (pg_blocking_pids).

Scenarios
  C1  Lost update on drug stock (read-modify-write)    + 3 fixes
  C2  Dirty read is impossible in PostgreSQL
  C3  Non-repeatable read of an invoice balance        READ COMMITTED vs REPEATABLE READ
  C4  Phantom read of a doctor's bookings              READ COMMITTED vs REPEATABLE READ
  C5  Write skew on the ICU emergency-bed reserve      REPEATABLE READ vs SERIALIZABLE
  C6  Double payment race at two cashier desks         unsafe check-then-insert vs record_payment()
  C7  Double booking race at two reception desks       EXCLUDE constraint (waits, then decides)
  C8  Concurrent admissions                            FOR UPDATE SKIP LOCKED
  C9  Deadlock between two pharmacists                 detection + prevention by lock ordering
  C10 Two records officers edit one patient            optimistic concurrency with xmin
  C11 One nightly billing job at a time                advisory lock

All demo rows are created in setup() and removed in cleanup(); the
database ends in the state it started in (apart from the audit trail).

Usage:  python concurrency_demos.py > results/concurrency_demos_output.txt
"""

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg

DSN = os.environ.get(
    "HOSPITAL_DSN",
    "postgresql://hospital_admin:hospital_pass@localhost:5432/stgrace_hospital",
)
BLOCK_WAIT = 0.6          # seconds to let a blocked statement settle before inspecting it

_print_lock = threading.Lock()
_t0 = time.perf_counter()
SUMMARY: list[tuple[str, str, str]] = []


def log(text: str = "") -> None:
    with _print_lock:
        print(text, flush=True)


def heading(code: str, title: str) -> None:
    global _t0
    _t0 = time.perf_counter()
    log()
    log("=" * 100)
    log(f"{code}  {title}")
    log("=" * 100)


def note(text: str) -> None:
    log(f"           # {text}")


def record(code: str, setting: str, outcome: str) -> None:
    SUMMARY.append((code, setting, outcome))
    log(f"           >>> RESULT: {outcome}")


class Session:
    """One database connection driven statement by statement."""

    def __init__(self, name: str):
        self.name = name
        self.conn = psycopg.connect(DSN, autocommit=True)   # BEGIN/COMMIT are sent explicitly
        self.cur = psycopg.ClientCursor(self.conn)          # client-side binding so the log shows real values
        self.cur.execute("SET search_path TO hospital, public")
        self.cur.execute("SET lock_timeout = '10s'")         # a demo must never hang forever
        self.pid = self.conn.info.backend_pid
        self.pool = ThreadPoolExecutor(max_workers=1)

    def _text(self, sql: str, params) -> str:
        return " ".join(self.cur.mogrify(sql, params).split())

    def _log(self, text: str) -> None:
        log(f"[{time.perf_counter() - _t0:6.2f}s] {self.name} | {text}")

    def run(self, sql: str, params=None, quiet: bool = False):
        """Execute one statement; returns rows (or None); raises psycopg.Error."""
        if not quiet:
            self._log(self._text(sql, params))
        try:
            self.cur.execute(sql, params)
        except psycopg.Error as e:
            self._log(f"  ERROR {e.sqlstate}: {e.diag.message_primary}")
            raise
        rows = self.cur.fetchall() if self.cur.description else None
        if rows is not None and not quiet:
            shown = rows[0] if len(rows) == 1 else rows
            self._log(f"  -> {shown}")
        return rows

    def value(self, sql: str, params=None):
        return self.run(sql, params)[0][0]

    def start(self, sql: str, params=None):
        """Run a statement that may block; returns a future (result or exception)."""
        self._log(self._text(sql, params) + "   (sent ...)")

        def job():
            try:
                self.cur.execute(sql, params)
                rows = self.cur.fetchall() if self.cur.description else None
                self._log(f"  ... completed -> {rows[0] if rows and len(rows) == 1 else rows}")
                return rows
            except psycopg.Error as e:
                self._log(f"  ... ERROR {e.sqlstate}: {e.diag.message_primary}")
                return e
        return self.pool.submit(job)

    def try_run(self, sql: str, params=None):
        """Run a statement that is expected to fail; returns the error (or None)."""
        try:
            self.run(sql, params)
            return None
        except psycopg.Error as e:
            return e

    def close(self) -> None:
        try:
            self.cur.execute("ROLLBACK")
        except psycopg.Error:
            pass
        self.pool.shutdown()
        self.conn.close()


class Monitor(Session):
    def show_blocking(self, waiter: Session) -> None:
        time.sleep(BLOCK_WAIT)
        rows = self.run("""
            SELECT a.pid, a.wait_event_type, a.wait_event,
                   pg_blocking_pids(a.pid) AS blocked_by, l.locktype, l.mode
            FROM pg_stat_activity a
            LEFT JOIN pg_locks l ON l.pid = a.pid AND NOT l.granted
            WHERE a.pid = %s""", (waiter.pid,), quiet=True)
        pid, etype, event, blockers, locktype, mode = rows[0]
        self._log(f"session {waiter.name} (pid {pid}) is waiting: wait_event={etype}/{event}, "
                  f"waiting for {locktype} {mode}, blocked by pid {blockers}")


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------
class Demo:
    """Ids of rows created for the demos."""


def setup(m: Monitor) -> Demo:
    d = Demo()
    m.run("BEGIN", quiet=True)
    d.drug = m.value("""INSERT INTO drugs (generic_name, dosage_form, strength, unit_price, stock_quantity)
                        VALUES ('DEMO Amoxicillin', 'Capsule', '500 mg', 60, 100) RETURNING drug_id""")
    d.drug2 = m.value("""INSERT INTO drugs (generic_name, dosage_form, strength, unit_price, stock_quantity)
                         VALUES ('DEMO Ceftriaxone', 'Injection', '1 g', 1500, 100) RETURNING drug_id""")
    d.enc, d.doc = m.run("""SELECT encounter_id, doctor_id FROM encounters
                            WHERE encounter_type = 'Outpatient' ORDER BY encounter_id LIMIT 1""")[0]
    d.patient = m.value("SELECT patient_id FROM encounters WHERE encounter_id = %s", (d.enc,))
    d.cashier = m.value("SELECT min(staff_id) FROM staff WHERE role = 'Cashier'")
    d.pharm = m.value("SELECT min(staff_id) FROM staff WHERE role = 'Pharmacist'")
    d.invoice = m.value("""INSERT INTO invoices (invoice_no, patient_id, encounter_id)
                           VALUES ('INV-DEMO-P3-C', %s, %s) RETURNING invoice_id""", (d.patient, d.enc))
    m.run("""INSERT INTO invoice_items (invoice_id, item_type, description, quantity, unit_price)
             VALUES (%s, 'Consultation', 'DEMO consultation', 1, 10000)""", (d.invoice,))
    d.free_patients = [r[0] for r in m.run("""
        SELECT p.patient_id FROM patients p
        WHERE NOT EXISTS (SELECT 1 FROM admissions a WHERE a.patient_id = p.patient_id AND a.discharged_at IS NULL)
        ORDER BY p.patient_id DESC LIMIT 2""")]
    d.icu_original = m.run("""SELECT b.bed_id, b.status FROM beds b JOIN wards w USING (ward_id)
                              WHERE w.ward_type = 'ICU' ORDER BY b.bed_id""")
    m.run("COMMIT", quiet=True)
    return d


def cleanup(m: Monitor, d: Demo) -> None:
    m.run("ROLLBACK", quiet=True)
    m.run("BEGIN", quiet=True)
    m.run("DELETE FROM payments WHERE invoice_id = %s", (d.invoice,), quiet=True)
    m.run("DELETE FROM invoice_items WHERE invoice_id = %s", (d.invoice,), quiet=True)
    m.run("DELETE FROM invoices WHERE invoice_id = %s", (d.invoice,), quiet=True)
    m.run("DELETE FROM appointments WHERE scheduled_start >= '2030-01-01' AND scheduled_start < '2031-01-01'",
          quiet=True)
    rx = [r[0] for r in m.run("SELECT DISTINCT prescription_id FROM prescription_items WHERE drug_id IN (%s, %s)",
                              (d.drug, d.drug2), quiet=True)]
    if rx:
        m.run("DELETE FROM prescription_items WHERE prescription_id = ANY(%s)", (rx,), quiet=True)
        m.run("DELETE FROM prescriptions WHERE prescription_id = ANY(%s)", (rx,), quiet=True)
    m.run("DELETE FROM drugs WHERE drug_id IN (%s, %s)", (d.drug, d.drug2), quiet=True)
    for bed_id, status in d.icu_original:
        m.run("UPDATE beds SET status = %s WHERE bed_id = %s", (status, bed_id), quiet=True)
    m.run("COMMIT", quiet=True)


def reset_stock(m: Monitor, d: Demo, qty: int = 100) -> None:
    m.run("UPDATE drugs SET stock_quantity = %s WHERE drug_id IN (%s, %s)", (qty, d.drug, d.drug2), quiet=True)


def reset_invoice(m: Monitor, d: Demo) -> None:
    m.run("DELETE FROM payments WHERE invoice_id = %s", (d.invoice,), quiet=True)
    m.run("UPDATE invoices SET status = 'Unpaid' WHERE invoice_id = %s", (d.invoice,), quiet=True)


def stock(m: Monitor, d: Demo) -> int:
    return m.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s", (d.drug,))


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def c1_lost_update(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C1", "Lost update: two pharmacists dispense DEMO Amoxicillin at the same time (stock 100, take 10 and 5)")
    note("Correct final stock is 100 - 10 - 5 = 85.")

    log("\n--- C1a  READ COMMITTED, application reads stock then writes an absolute value")
    reset_stock(m, d)
    a.run("BEGIN ISOLATION LEVEL READ COMMITTED")
    b.run("BEGIN ISOLATION LEVEL READ COMMITTED")
    sa = a.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s", (d.drug,))
    sb = b.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s", (d.drug,))
    a.run("UPDATE drugs SET stock_quantity = %s WHERE drug_id = %s", (sa - 10, d.drug))
    a.run("COMMIT")
    b.run("UPDATE drugs SET stock_quantity = %s WHERE drug_id = %s", (sb - 5, d.drug))
    b.run("COMMIT")
    final = stock(m, d)
    m._log(f"final stock = {final}")
    record("C1a", "READ COMMITTED, read-modify-write", f"ANOMALY: stock {final}, A's 10 capsules vanished from the books")

    log("\n--- C1b  Fix 1: relative UPDATE (stock = stock - n): the second writer waits and re-reads")
    reset_stock(m, d)
    a.run("BEGIN")
    b.run("BEGIN")
    a.run("UPDATE drugs SET stock_quantity = stock_quantity - 10 WHERE drug_id = %s", (d.drug,))
    fut = b.start("UPDATE drugs SET stock_quantity = stock_quantity - 5 WHERE drug_id = %s", (d.drug,))
    m.show_blocking(b)
    a.run("COMMIT")
    fut.result()
    b.run("COMMIT")
    final = stock(m, d)
    m._log(f"final stock = {final}")
    record("C1b", "relative UPDATE (row lock)", f"PREVENTED: stock {final}")

    log("\n--- C1c  Fix 2: pessimistic lock, SELECT ... FOR UPDATE before computing")
    reset_stock(m, d)
    a.run("BEGIN")
    b.run("BEGIN")
    sa = a.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s FOR UPDATE", (d.drug,))
    fut = b.start("SELECT stock_quantity FROM drugs WHERE drug_id = %s FOR UPDATE", (d.drug,))
    m.show_blocking(b)
    a.run("UPDATE drugs SET stock_quantity = %s WHERE drug_id = %s", (sa - 10, d.drug))
    a.run("COMMIT")
    sb = fut.result()[0][0]
    note(f"B's locked read returned {sb}, A's committed value, not the stale 100")
    b.run("UPDATE drugs SET stock_quantity = %s WHERE drug_id = %s", (sb - 5, d.drug))
    b.run("COMMIT")
    final = stock(m, d)
    m._log(f"final stock = {final}")
    record("C1c", "SELECT ... FOR UPDATE", f"PREVENTED: stock {final}")

    log("\n--- C1d  Fix 3: REPEATABLE READ detects the conflict; the application retries")
    reset_stock(m, d)
    a.run("BEGIN ISOLATION LEVEL REPEATABLE READ")
    b.run("BEGIN ISOLATION LEVEL REPEATABLE READ")
    sa = a.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s", (d.drug,))
    sb = b.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s", (d.drug,))
    a.run("UPDATE drugs SET stock_quantity = %s WHERE drug_id = %s", (sa - 10, d.drug))
    a.run("COMMIT")
    err = b.try_run("UPDATE drugs SET stock_quantity = %s WHERE drug_id = %s", (sb - 5, d.drug))
    b.run("ROLLBACK")
    note(f"B got SQLSTATE {err.sqlstate} (serialization_failure) - retrying the whole transaction")
    b.run("BEGIN ISOLATION LEVEL REPEATABLE READ")
    sb = b.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s", (d.drug,))
    b.run("UPDATE drugs SET stock_quantity = %s WHERE drug_id = %s", (sb - 5, d.drug))
    b.run("COMMIT")
    final = stock(m, d)
    m._log(f"final stock = {final}")
    record("C1d", "REPEATABLE READ + retry", f"PREVENTED: stock {final} (1 retry)")


def c2_dirty_read(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C2", "Dirty read: B asks for READ UNCOMMITTED while A has an uncommitted change")
    reset_stock(m, d)
    a.run("BEGIN")
    a.run("UPDATE drugs SET stock_quantity = 0 WHERE drug_id = %s", (d.drug,))
    b.run("BEGIN ISOLATION LEVEL READ UNCOMMITTED")
    b.run("SHOW transaction_isolation")
    seen = b.value("SELECT stock_quantity FROM drugs WHERE drug_id = %s", (d.drug,))
    b.run("COMMIT")
    a.run("ROLLBACK")
    note("PostgreSQL treats READ UNCOMMITTED as READ COMMITTED: MVCC never exposes uncommitted versions")
    record("C2", "READ UNCOMMITTED", f"PREVENTED: B saw {seen}, not A's uncommitted 0")


def c3_non_repeatable_read(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C3", "Non-repeatable read: a cashier prints a statement while another desk takes a payment")
    q = "SELECT balance_due FROM v_invoice_balance WHERE invoice_id = %s"
    for level in ("READ COMMITTED", "REPEATABLE READ"):
        log(f"\n--- C3 {level}")
        reset_invoice(m, d)
        a.run(f"BEGIN ISOLATION LEVEL {level}")
        first = a.value(q, (d.invoice,))
        b.run("SELECT record_payment(%s, 4000, 'Cash', %s)", (d.invoice, d.cashier))
        note("B's call runs in autocommit mode, so the payment is committed immediately")
        second = a.value(q, (d.invoice,))
        a.run("COMMIT")
        if first != second:
            record("C3", level, f"ANOMALY: same query in one transaction returned {first} then {second}")
        else:
            record("C3", level, f"PREVENTED: both reads returned {first} (snapshot taken at first query)")


def c4_phantom(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C4", "Phantom read: a manager counts Dr's bookings for a day while reception adds one")
    q = """SELECT COUNT(*) FROM appointments WHERE doctor_id = %s
           AND scheduled_start >= '2030-02-05' AND scheduled_start < '2030-02-06'"""
    for i, level in enumerate(("READ COMMITTED", "REPEATABLE READ")):
        log(f"\n--- C4 {level}")
        a.run(f"BEGIN ISOLATION LEVEL {level}")
        first = a.value(q, (d.doc,))
        b.run("SELECT book_appointment(%s, %s, %s)", (d.patient, d.doc, f"2030-02-05 {9 + i}:00"))
        second = a.value(q, (d.doc,))
        a.run("COMMIT")
        if first != second:
            record("C4", level, f"ANOMALY: count went {first} -> {second} (a phantom row appeared)")
        else:
            record("C4", level, f"PREVENTED: count stayed {first} (PostgreSQL's RR also blocks phantoms)")


def c5_write_skew(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C5", "Write skew: rule 'always keep at least 1 ICU bed free for emergencies'")
    note("Each nurse checks that >= 2 ICU beds are free, then takes a DIFFERENT bed. No row is written twice.")
    free = [r[0] for r in m.run("""SELECT b.bed_id FROM beds b JOIN wards w USING (ward_id)
                                   WHERE w.ward_type = 'ICU' AND b.status = 'Available' ORDER BY b.bed_id""",
                                quiet=True)]
    count_q = """SELECT COUNT(*) FROM beds b JOIN wards w USING (ward_id)
                 WHERE w.ward_type = 'ICU' AND b.status = 'Available'"""
    for level in ("REPEATABLE READ", "SERIALIZABLE"):
        log(f"\n--- C5 {level}")
        # leave exactly two ICU beds available (the rest temporarily under maintenance)
        for bed_id, status in d.icu_original:
            m.run("UPDATE beds SET status = %s WHERE bed_id = %s",
                  ('Available' if bed_id in free[:2] else ('Maintenance' if status == 'Available' else status), bed_id),
                  quiet=True)
        m._log(f"setup: ICU beds available = {m.run(count_q, quiet=True)[0][0]}")
        a.run(f"BEGIN ISOLATION LEVEL {level}")
        b.run(f"BEGIN ISOLATION LEVEL {level}")
        ca = a.value(count_q)
        cb = b.value(count_q)
        note(f"both see {ca} free, so both decide admission is allowed")
        a.run("UPDATE beds SET status = 'Occupied' WHERE bed_id = %s", (free[0],))
        b.run("UPDATE beds SET status = 'Occupied' WHERE bed_id = %s", (free[1],))
        a.run("COMMIT")
        err = b.try_run("COMMIT")
        if err:
            b.run("ROLLBACK")
        left = m.run(count_q, quiet=True)[0][0]
        m._log(f"ICU beds still free after both nurses: {left}")
        if left == 0:
            record("C5", level, "ANOMALY: both committed, 0 emergency beds left")
        else:
            record("C5", level, f"PREVENTED: B's COMMIT failed with {err.sqlstate} (SSI detected the rw-dependency cycle); "
                                f"{left} bed kept free, B must retry and will now see only 1 free")


def c6_double_payment(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C6", "Double payment: two cashiers settle the same NGN 10,000 bill at the same moment")
    bal = "SELECT balance_due FROM v_invoice_balance WHERE invoice_id = %s"

    log("\n--- C6a  Unsafe: check the balance, then insert the payment (no lock)")
    reset_invoice(m, d)
    a.run("BEGIN")
    b.run("BEGIN")
    ba = a.value(bal, (d.invoice,))
    bb = b.value(bal, (d.invoice,))
    a.run("INSERT INTO payments (invoice_id, amount, method, received_by) VALUES (%s, %s, 'Cash', %s)",
          (d.invoice, ba, d.cashier))
    b.run("INSERT INTO payments (invoice_id, amount, method, received_by) VALUES (%s, %s, 'POS', %s)",
          (d.invoice, bb, d.cashier))
    a.run("COMMIT")
    b.run("COMMIT")
    final = m.value(bal, (d.invoice,))
    m._log(f"balance_due = {final}")
    record("C6a", "check-then-insert, no lock", f"ANOMALY: patient charged twice, balance {final}")

    log("\n--- C6b  Safe: record_payment() locks the invoice row first")
    reset_invoice(m, d)
    a.run("BEGIN")
    b.run("BEGIN")
    a.run("SELECT record_payment(%s, 10000, 'Cash', %s)", (d.invoice, d.cashier))
    fut = b.start("SELECT record_payment(%s, 10000, 'POS', %s)", (d.invoice, d.cashier))
    m.show_blocking(b)
    a.run("COMMIT")
    res = fut.result()
    b.run("ROLLBACK")
    final = m.value(bal, (d.invoice,))
    m._log(f"balance_due = {final}")
    outcome = "PREVENTED: second payment rejected" if isinstance(res, psycopg.Error) else "UNEXPECTED"
    record("C6b", "record_payment() with FOR UPDATE", f"{outcome}, balance {final}")


def c7_double_booking(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C7", "Double booking: two receptionists book the same doctor's 10:00 slot")
    slot = "2030-02-04 10:00"
    for outcome_a in ("COMMIT", "ROLLBACK"):
        log(f"\n--- C7 A decides {outcome_a}")
        a.run("BEGIN")
        b.run("BEGIN")
        a.run("SELECT book_appointment(%s, %s, %s)", (d.patient, d.doc, slot))
        fut = b.start("SELECT book_appointment(%s, %s, %s)", (d.free_patients[0], d.doc, slot))
        m.show_blocking(b)
        note("B cannot know yet whether the slot is free: it waits for A's transaction to finish")
        a.run(outcome_a)
        res = fut.result()
        b.run("ROLLBACK" if isinstance(res, psycopg.Error) else "COMMIT")
        n = m.value("SELECT COUNT(*) FROM appointments WHERE doctor_id = %s AND scheduled_start = %s", (d.doc, slot))
        if outcome_a == "COMMIT":
            record("C7", "A commits", f"PREVENTED: B rejected after waiting; {n} booking in the slot")
        else:
            record("C7", "A rolls back", f"B's booking went through once A released the slot; {n} booking in the slot")
        m.run("DELETE FROM appointments WHERE doctor_id = %s AND scheduled_start = %s", (d.doc, slot), quiet=True)


def c8_skip_locked(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C8", "Concurrent admissions: two nurses admit patients to the General ward at the same time")
    a.run("BEGIN")
    b.run("BEGIN")
    adm_a = a.value("SELECT admit_patient(%s, 'General', %s, 'DEMO concurrent admission')", (d.free_patients[0], d.doc))
    t = time.perf_counter()
    adm_b = b.value("SELECT admit_patient(%s, 'General', %s, 'DEMO concurrent admission')", (d.free_patients[1], d.doc))
    waited = (time.perf_counter() - t) * 1000
    bed_a = a.value("SELECT bed_id FROM admissions WHERE admission_id = %s", (adm_a,))
    bed_b = b.value("SELECT bed_id FROM admissions WHERE admission_id = %s", (adm_b,))
    a.run("ROLLBACK")
    b.run("ROLLBACK")
    note("both transactions rolled back - nothing is left behind")
    record("C8", "FOR UPDATE SKIP LOCKED",
           f"A got bed {bed_a}, B got bed {bed_b} in {waited:.0f} ms without waiting for A")


def c9_deadlock(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C9", "Deadlock: two pharmacists update the same two drugs in opposite order")
    reset_stock(m, d)
    log("\n--- C9a  Opposite lock order")
    a.run("BEGIN")
    b.run("BEGIN")
    a.run("UPDATE drugs SET stock_quantity = stock_quantity - 1 WHERE drug_id = %s", (d.drug,))
    b.run("UPDATE drugs SET stock_quantity = stock_quantity - 1 WHERE drug_id = %s", (d.drug2,))
    fa = a.start("UPDATE drugs SET stock_quantity = stock_quantity - 1 WHERE drug_id = %s", (d.drug2,))
    m.show_blocking(a)
    fb = b.start("UPDATE drugs SET stock_quantity = stock_quantity - 1 WHERE drug_id = %s", (d.drug,))
    note("A waits for B and B waits for A; after deadlock_timeout (1 s) PostgreSQL picks a victim")
    ra, rb = fa.result(), fb.result()
    victim, survivor = (b, a) if isinstance(rb, psycopg.Error) else (a, b)
    victim_err = rb if victim is b else ra
    victim.run("ROLLBACK")
    survivor.run("COMMIT")
    record("C9a", "opposite lock order", f"DEADLOCK detected: session {victim.name} aborted with {victim_err.sqlstate}, "
                                          f"session {survivor.name} committed")

    log("\n--- C9b  Prevention: dispense_prescription() always locks drugs in drug_id order")
    reset_stock(m, d)
    rx = []
    for first, second in ((d.drug2, d.drug), (d.drug, d.drug2)):    # items listed in opposite order
        rid = m.value("INSERT INTO prescriptions (encounter_id, prescribed_by) VALUES (%s, %s) RETURNING prescription_id",
                      (d.enc, d.doc))
        for drug in (first, second):
            m.run("""INSERT INTO prescription_items (prescription_id, drug_id, dose, frequency, duration_days, quantity)
                     VALUES (%s, %s, '1', 'Once daily', 1, 1)""", (rid, drug), quiet=True)
        rx.append(rid)
    a.run("BEGIN")
    b.run("BEGIN")
    a.run("SELECT dispense_prescription(%s, %s)", (rx[0], d.pharm))
    fb = b.start("SELECT dispense_prescription(%s, %s)", (rx[1], d.pharm))
    m.show_blocking(b)
    a.run("COMMIT")
    rb = fb.result()
    b.run("ROLLBACK" if isinstance(rb, psycopg.Error) else "COMMIT")
    record("C9b", "consistent lock order",
           "PREVENTED: B simply waited for A, then completed" if not isinstance(rb, psycopg.Error) else "UNEXPECTED")


def c10_optimistic(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C10", "Lost update on a form: two records officers edit the same patient's phone number")
    note("No lock is held while a person types. Each officer remembers the row version (xmin) they loaded.")
    pid = d.free_patients[0]
    original = m.value("SELECT phone FROM patients WHERE patient_id = %s", (pid,))
    phone_a, ver_a = a.run("SELECT phone, xmin::text FROM patients WHERE patient_id = %s", (pid,))[0]
    phone_b, ver_b = b.run("SELECT phone, xmin::text FROM patients WHERE patient_id = %s", (pid,))[0]
    upd = "UPDATE patients SET phone = %s WHERE patient_id = %s AND xmin = %s::xid RETURNING xmin::text"
    ra = a.run(upd, ("08031111111", pid, ver_a))
    rb = b.run(upd, ("08032222222", pid, ver_b))
    note("B's UPDATE matched 0 rows: the version B loaded no longer exists, so B is told to reload")
    final = m.value("SELECT phone FROM patients WHERE patient_id = %s", (pid,))
    m.run("UPDATE patients SET phone = %s WHERE patient_id = %s", (original, pid), quiet=True)
    record("C10", "optimistic (xmin version check)",
           f"PREVENTED: A saved ({len(ra)} row), B's stale save rejected ({len(rb)} rows); phone = {final}")


def c11_advisory(a: Session, b: Session, m: Monitor, d: Demo) -> None:
    heading("C11", "Only one nightly billing run at a time (advisory lock on key 8103)")
    a.run("SELECT pg_try_advisory_lock(8103) AS got_lock")
    got_b = b.value("SELECT pg_try_advisory_lock(8103) AS got_lock")
    note("B's scheduler sees the job is already running and skips this cycle")
    a.run("SELECT pg_advisory_unlock(8103)")
    b.run("SELECT pg_try_advisory_lock(8103) AS got_lock")
    b.run("SELECT pg_advisory_unlock(8103)")
    record("C11", "advisory lock", f"PREVENTED: second job instance refused (got_lock = {got_b})")


def main() -> None:
    m = Monitor("M")
    a, b = Session("A"), Session("B")
    log(f"Sessions: A = pid {a.pid}, B = pid {b.pid}, monitor M = pid {m.pid}")
    log(f"PostgreSQL {m.value('SHOW server_version')}, default isolation "
        f"{m.value('SHOW default_transaction_isolation')}, deadlock_timeout {m.value('SHOW deadlock_timeout')}")
    d = setup(m)
    try:
        for scenario in (c1_lost_update, c2_dirty_read, c3_non_repeatable_read, c4_phantom, c5_write_skew,
                         c6_double_payment, c7_double_booking, c8_skip_locked, c9_deadlock,
                         c10_optimistic, c11_advisory):
            scenario(a, b, m, d)
            for s in (a, b):
                s.run("ROLLBACK", quiet=True)          # never carry an open transaction into the next demo
    finally:
        cleanup(m, d)
        for s in (a, b, m):
            s.close()

    log()
    log("=" * 100)
    log("SUMMARY")
    log("=" * 100)
    for code, setting, outcome in SUMMARY:
        log(f"{code:<5} {setting:<36} {outcome}")


if __name__ == "__main__":
    sys.exit(main())
