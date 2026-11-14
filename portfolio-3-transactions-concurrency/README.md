# Portfolio 3 — Transactions and Concurrency

**Case study:** St. Grace Specialist Hospital
**DBMS:** PostgreSQL 18.6 (MVCC; default isolation READ COMMITTED; `deadlock_timeout` = 1 s)

A hospital is a highly concurrent system: several pharmacists draw on one drug store,
cashiers at different desks settle the same bills, receptionists book the same doctors,
and nurses compete for the same beds. This portfolio shows what goes wrong when those
operations interleave, and the database mechanisms that stop it.

## Contents

| File | Purpose |
|---|---|
| `sql/01_transaction_objects.sql` | Audit trail (table + generic trigger) and 4 business transactions as PL/pgSQL functions |
| `sql/02_transaction_basics.sql` | Single-session demos: COMMIT, automatic and explicit ROLLBACK, SAVEPOINT, MVCC, statement atomicity |
| `concurrency_demos.py` | 11 two-session scenarios driven in a fixed interleaving, with a timeline and lock monitoring |
| `results/transaction_basics_output.txt` | Output of the single-session demos |
| `results/concurrency_demos_output.txt` | Full timeline of every concurrency scenario, plus a summary |
| `results/server_log_deadlock.txt` | PostgreSQL's own server-log entry for the deadlock in C9 |
| `results/audit_log_sample.txt` | Audit-trail rows written during the demos |

Every demo creates its own rows and removes them afterwards. After the run, row counts,
ICU bed statuses and the edited patient record matched their original values, and all six
Portfolio 1 consistency checks still reported 0 violations. Only the audit trail is kept,
because audit logs are append-only by design.

## Transactional building blocks (`01_transaction_objects.sql`)

| Function | Business operation | Concurrency control used |
|---|---|---|
| `dispense_prescription(rx, pharmacist)` | Decrement stock for every item, mark the prescription Dispensed | `FOR UPDATE` on the prescription; drug rows locked **in `drug_id` order**, so callers can never deadlock |
| `admit_patient(patient, ward_type, doctor, reason)` | Choose a bed, create the admission and inpatient encounter, mark the bed Occupied | `FOR UPDATE OF b SKIP LOCKED`: a bed someone else is assigning is skipped, not waited for |
| `record_payment(invoice, amount, method, staff)` | Check the balance, insert the payment, set Paid or Partial | `FOR UPDATE` on the invoice, so the check and the insert can't interleave with another cashier |
| `book_appointment(patient, doctor, start)` | Insert an appointment | The `EXCLUDE` constraint from Portfolio 1; the wrapper only turns the error into a clear message |

Each function is **one atomic unit**. If a later step raises an error, earlier steps in the
same transaction are undone too. The `audit_log` trigger records who changed what,
storing only the changed columns, and records each change's `txid`, so all rows written
by one transaction can be grouped together.

## Part A — single-session transactions (`02_transaction_basics.sql`)

| Demo | What happened | What it shows |
|---|---|---|
| **T1 COMMIT** | Dispensed 6 Paracetamol + 2 Artesunate. Inside the transaction the session already saw stock 94 and 8. After COMMIT it was permanent, and the audit trail shows both updates under txid 1317. | Durability; a transaction sees its own uncommitted writes |
| **T2 Automatic ROLLBACK** | Paracetamol was decremented first, then `Insufficient stock of DEMO Artesunate: need 20, have 8` was raised. The next statement got `current transaction is aborted`. After ROLLBACK, Paracetamol was **still 94** and the prescription was still Pending. | **Atomicity**: work already done is undone when a later step fails |
| **T3 Explicit ROLLBACK** | A price was mistyped as ₦200 instead of ₦20; ROLLBACK restored ₦20. | The user can cancel a whole unit of work |
| **T4 SAVEPOINT** | Discharge, bed release and invoice succeeded. The insurance step failed (₦999,999 > ₦30,000 balance). `ROLLBACK TO SAVEPOINT` undid only that step, a ₦15,000 POS payment was taken instead, and COMMIT kept everything else. | Partial rollback inside a transaction |
| **T5 MVCC** | Before: `xmin=1317, ctid=(0,23)`. During the update: `xmin=1323` (this transaction), `ctid=(0,27)`, a **new row version**. After ROLLBACK the old version is current again, with `xmax=1323`. | PostgreSQL never updates in place; readers see a consistent version without locking. The initial non-zero `xmax=1319` comes from the row lock taken by T2's rolled-back transaction, because row locks are also recorded in `xmax`. |
| **T6 Statement atomicity** | `UPDATE … SET stock = stock - 50 WHERE drug_id IN (Paracetamol, Artesunate)` failed the CHECK on Artesunate, and **Paracetamol was unchanged**. | A single statement is all-or-nothing, even in autocommit mode |

## Part B — concurrency (`concurrency_demos.py`)

Two real connections, **A** and **B**, run their statements in a fixed order. When B is
expected to block, its statement runs on a background thread. A third **monitor** session
then queries `pg_stat_activity`, `pg_locks` and `pg_blocking_pids()` to show who is waiting
for whom.

### Summary of results

| # | Hospital situation | Setting | Outcome |
|---|---|---|---|
| C1a | Two pharmacists dispense the same drug (stock 100, take 10 and 5) | READ COMMITTED, app reads then writes absolute value | ❌ **Lost update**: stock 95 instead of 85 |
| C1b | (same) | `UPDATE … SET stock = stock - n` | ✅ 85. B waited on A's row lock, then re-read the new value |
| C1c | (same) | `SELECT … FOR UPDATE` | ✅ 85. B's locked read returned 90, not the stale 100 |
| C1d | (same) | REPEATABLE READ | ✅ 85. B got `40001 could not serialize access due to concurrent update` and retried once |
| C2 | Reading another desk's unsaved change | READ UNCOMMITTED | ✅ **No dirty read**: B saw 100, not A's uncommitted 0 |
| C3 | Cashier prints a statement while a payment is taken | READ COMMITTED | ❌ **Non-repeatable read**: balance 10,000 then 6,000 in one transaction |
| C3 | (same) | REPEATABLE READ | ✅ Both reads 10,000 (one snapshot for the whole transaction) |
| C4 | Manager counts a doctor's bookings while one is added | READ COMMITTED | ❌ **Phantom**: count 0 → 1 |
| C4 | (same) | REPEATABLE READ | ✅ Count stayed 1 |
| C5 | Rule: keep ≥ 1 ICU bed free. Two nurses each see 2 free and take *different* beds | REPEATABLE READ | ❌ **Write skew**: both committed, 0 emergency beds left |
| C5 | (same) | SERIALIZABLE | ✅ B's COMMIT failed with `40001 … read/write dependencies among transactions`; 1 bed kept free |
| C6a | Two cashiers settle the same ₦10,000 bill | Check balance, then insert (no lock) | ❌ **Double charge**: balance −₦10,000 |
| C6b | (same) | `record_payment()` with `FOR UPDATE` | ✅ B waited, then was rejected: "exceeds outstanding balance of 0.00" |
| C7 | Two receptionists book the same 10:00 slot | EXCLUDE constraint, A commits | ✅ B **waited for A**, then was rejected (`23P01`) |
| C7 | (same) | EXCLUDE constraint, A rolls back | ✅ B's booking went through as soon as A released the slot |
| C8 | Two nurses admit patients at the same moment | `FOR UPDATE SKIP LOCKED` | ✅ A got bed 21, B got bed 22, with **no waiting** (31 ms) |
| C9a | Two pharmacists update drugs X,Y and Y,X | Opposite lock order | ⚠️ **Deadlock** detected after 1 s; A aborted (`40P01`), B committed |
| C9b | (same drugs, via `dispense_prescription`) | Consistent lock order | ✅ B simply waited for A, then completed; no deadlock |
| C10 | Two records officers edit one patient's phone number | Optimistic check on `xmin` | ✅ A saved; B's stale save matched 0 rows and was rejected |
| C11 | Nightly billing job started twice | Advisory lock | ✅ Second instance got `false` and skipped |

The full timelines, with every statement, result, and lock wait, are in
`results/concurrency_demos_output.txt`. For example, C6b:

```
[  0.04s] A | SELECT record_payment(172710, 10000, 'Cash', 321)
[  0.05s] A |   -> ('Paid',)
[  0.05s] B | SELECT record_payment(172710, 10000, 'POS', 321)   (sent ...)
[  0.66s] M | session B (pid 22424) is waiting: wait_event=Lock/transactionid, waiting for transactionid ShareLock, blocked by pid [14900]
[  0.66s] A | COMMIT
[  0.66s] B |   ... ERROR 23514: Payment of 10000 exceeds outstanding balance of 0.00 on invoice 172710
```

## Analysis

**1. READ COMMITTED is the default, and it allows three of the anomalies shown.** Each
statement sees data committed before *that statement* started. That's why C3 (balance
changed mid-report), C4 (phantom appointment) and C1a (lost update) all happened under
the default setting. Most application code runs at this level, so the lesson is that the
default alone does not protect read-then-write logic.

**2. PostgreSQL's isolation levels are stronger than the SQL standard requires.**

| Anomaly | SQL standard allows at RR? | PostgreSQL RR | Evidence |
|---|---|---|---|
| Dirty read | No (allowed only at READ UNCOMMITTED) | Never, at any level | C2: READ UNCOMMITTED still saw 100 |
| Phantom read | **Yes** | **No** (snapshot isolation) | C4: count stayed 1 |
| Write skew | Yes | **Yes** | C5: 0 ICU beds left |

PostgreSQL's REPEATABLE READ is *snapshot isolation*. It prevents phantoms but not
write skew, because write skew involves two transactions writing **different rows**, so no
write-write conflict ever occurs. Only SERIALIZABLE catches it. PostgreSQL's
**Serializable Snapshot Isolation (SSI)** tracks what each transaction *read* (SIREAD
predicate locks) and aborts one transaction when reads and writes form a dangerous cycle.
Unlike two-phase locking, it never blocks readers. It aborts at commit time instead.

**3. There are two ways to stop a lost update, with different trade-offs.**

| Approach | How it behaved | When to use it |
|---|---|---|
| Pessimistic (`FOR UPDATE`, or a relative `UPDATE`) | B waited about 0.6 s, then continued with the correct value | Short, high-contention operations: stock, payments, beds |
| Isolation-based (REPEATABLE READ / SERIALIZABLE) | B failed immediately with `40001`, then retried once | When the application has a retry loop; good when conflicts are rare |
| Optimistic (`xmin` version check, C10) | No locks at all; B's save matched 0 rows | When a *person* holds a record on screen for minutes. A lock held that long would block everyone else |

Choosing an isolation level is therefore also choosing where errors appear. Stronger
isolation means **the application must retry on SQLSTATE 40001**, otherwise users see
failures that look random.

**4. Constraints are concurrency control too.** In C7 there is no explicit lock in the
code, yet B's booking **waited** for A's transaction, because the exclusion index entry
was not yet committed. B was then rejected if A committed, or allowed through if A rolled
back. A check-then-insert in application code ("is the slot free? then book it") can't do
this: both receptionists would see the slot free. Putting the rule in the database
(Portfolio 1) is what makes it hold under concurrency.

**5. SKIP LOCKED turns a queue into parallel work.** Without it, the second nurse in C8
would wait for the first nurse's transaction to finish, only to discover that bed 21 was
taken. With it, B immediately took bed 22. The same pattern suits lab work lists and
pharmacy queues.

**6. Deadlocks are detected, but prevention is better.** In C9a PostgreSQL ran its
deadlock check after `deadlock_timeout` (1 s), found the cycle (the server log records
"Process X waits for ShareLock on transaction 1473; blocked by process Y", and the
reverse) and aborted one transaction. Detection costs the victim its work and costs
everyone a one-second stall. `dispense_prescription` prevents the problem by always
locking drugs in `drug_id` order. C9b uses two prescriptions listing the same drugs in
opposite order, and the second pharmacist simply waited instead of deadlocking.

**7. An audit trail makes concurrency bugs visible after the fact.** The double payment
in C6a appears in `audit_log` as two `payments INSERT` rows for the same invoice from
**different transactions** (txid 1453 and 1454), 3 ms apart. An auditor can reconstruct
exactly what happened.

## Recommendations for the hospital system

| Operation | Recommended mechanism | Demonstrated in |
|---|---|---|
| Dispensing drugs | Relative `UPDATE`, or `FOR UPDATE` in fixed `drug_id` order | C1b, C1c, C9b |
| Taking payments | Lock the invoice row (`record_payment`) | C6b |
| Booking appointments | `EXCLUDE` constraint | C7 |
| Assigning beds | `FOR UPDATE SKIP LOCKED` | C8 |
| Rules spanning several rows (ICU reserve, on-call cover) | SERIALIZABLE + retry loop | C5 |
| Editing patient records in a form | Optimistic check on `xmin` (or a version column) | C10 |
| Reports needing one consistent picture | REPEATABLE READ (read-only) | C3, C4 |
| Single-instance batch jobs | Advisory lock | C11 |

## How to run

```powershell
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
& $psql -h localhost -U hospital_admin -d stgrace_hospital -f sql\01_transaction_objects.sql
& $psql -h localhost -U hospital_admin -d stgrace_hospital -f sql\02_transaction_basics.sql > results\transaction_basics_output.txt 2>&1
python concurrency_demos.py > results\concurrency_demos_output.txt
```

The concurrency demos take about 10 seconds. Re-running the Portfolio 1 data generator
drops and rebuilds the schema, so run `01_transaction_objects.sql` again afterwards.
