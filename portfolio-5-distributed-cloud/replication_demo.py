"""
Portfolio 5: replication, consistency and availability on a 3-member replica set.

Requires:  python cluster.py start rs

  R0  Topology and configuration
  R1  Load hospital data; every member holds an identical copy; the oplog
  R2  Write concern: latency cost of w:0, w:1, j:true, w:majority, w:3
  R3  Replication lag made visible (one secondary paused with a fail point):
      stale secondary reads, w:majority still succeeds, w:3 times out,
      causal consistency refuses a stale read
  R4  Multi-document ACID transactions (failed on the standalone in Portfolio 4)
  R5  Automatic failover: the primary process is killed while a client writes
  R6  Loss of majority: writes stop, reads continue (the CAP trade-off)

Usage:  python replication_demo.py > results/replication_demo_output.txt
"""

import os
import signal
import statistics
import threading
import time
from datetime import datetime

from bson.decimal128 import Decimal128
from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.errors import OperationFailure, PyMongoError, WTimeoutError
from pymongo.read_concern import ReadConcern

import cluster

HOST = "127.0.0.1"
PORTS = [27018, 27019, 27020]
URI = f"mongodb://{HOST}:27018,{HOST}:27019,{HOST}:27020/?replicaSet=rs0"
SOURCE = MongoClient("mongodb://localhost:27017")["stgrace_hospital"]      # Portfolio 4 standalone
T0 = time.perf_counter()


def log(text: str = "") -> None:
    print(f"[{time.perf_counter() - T0:7.2f}s] {text}" if text else "", flush=True)


def heading(code: str, title: str) -> None:
    print()
    print("=" * 96)
    print(f"{code}  {title}")
    print("=" * 96, flush=True)


def direct(port: int, **kw) -> MongoClient:
    return MongoClient(HOST, port, directConnection=True, serverSelectionTimeoutMS=3000, **kw)


def member_states() -> str:
    parts = []
    for port in PORTS:
        try:
            h = direct(port).admin.command("hello")
            parts.append(f":{port} {'PRIMARY' if h.get('isWritablePrimary') else 'SECONDARY' if h.get('secondary') else 'OTHER'}")
        except PyMongoError:
            parts.append(f":{port} DOWN")
    return "  ".join(parts)


def primary_port() -> int | None:
    for port in PORTS:
        try:
            if direct(port).admin.command("hello").get("isWritablePrimary"):
                return port
        except PyMongoError:
            pass
    return None


# ---------------------------------------------------------------------------
def r0_topology(client: MongoClient) -> None:
    heading("R0", "Replica set topology")
    cfg = client.admin.command("replSetGetConfig")["config"]
    st = client.admin.command("replSetGetStatus")
    log(f"set '{cfg['_id']}', protocolVersion {cfg['protocolVersion']}, "
        f"electionTimeoutMillis {cfg['settings']['electionTimeoutMillis']}, "
        f"heartbeatIntervalMillis {cfg['settings']['heartbeatIntervalMillis']}")
    for m in cfg["members"]:
        s = next(x for x in st["members"] if x["_id"] == m["_id"])
        log(f"  member {m['_id']}  {m['host']:<16} priority {m['priority']}  votes {m['votes']}  {s['stateStr']}")
    log(f"majority = {st['majorityVoteCount']} of {len(cfg['members'])} votes; "
        f"the set tolerates {len(cfg['members']) - st['majorityVoteCount']} failed member(s)")


def r1_load_and_replicate(client: MongoClient) -> None:
    heading("R1", "Load hospital data through the primary; it is replicated to every member")
    db = client.get_database("stgrace_hospital", write_concern=WriteConcern("majority"))
    db.drop_collection("patients")
    db.drop_collection("encounters")
    db.drop_collection("appointments")
    start = time.time()
    patients = list(SOURCE.patients.find())
    for i in range(0, len(patients), 5000):
        db.patients.insert_many(patients[i:i + 5000])
    encounters = list(SOURCE.encounters.find({"date": {"$gte": datetime(2026, 5, 1)}}))
    for i in range(0, len(encounters), 5000):
        db.encounters.insert_many(encounters[i:i + 5000])
    db.encounters.create_index([("patient.patient_id", 1), ("date", -1)])
    log(f"loaded {len(patients):,} patients and {len(encounters):,} encounters (May-Nov 2026) "
        f"with w:majority in {time.time() - start:.1f}s")
    time.sleep(1)
    for port in PORTS:
        mdb = direct(port)["stgrace_hospital"]
        h = direct(port).admin.command("hello")
        dbh = mdb.command("dbHash", collections=["patients", "encounters"])
        log(f"  :{port} {'PRIMARY  ' if h.get('isWritablePrimary') else 'SECONDARY'} patients={mdb.patients.estimated_document_count():,} "
            f"encounters={mdb.encounters.estimated_document_count():,} dbHash(encounters)={dbh['collections']['encounters'][:12]}")
    log("identical hashes = byte-identical data on all three members")

    oplog = client.local["oplog.rs"]
    first = oplog.find_one(sort=[("$natural", 1)])["ts"]
    last = oplog.find_one(sort=[("$natural", -1)])["ts"]
    stats = client.local.command("collStats", "oplog.rs")
    log(f"oplog: {oplog.estimated_document_count():,} entries, {stats['size'] / 1048576:.1f} MB used of "
        f"{stats['maxSize'] / 1048576:.0f} MB cap, window {last.time - first.time} s")
    db.patients.update_one({"_id": 1}, {"$set": {"contact.phone": "08035550101"}})
    entry = oplog.find_one({"ns": "stgrace_hospital.patients", "op": "u"}, sort=[("$natural", -1)])
    log(f"oplog entry for a phone-number update: op={entry['op']} ns={entry['ns']} o2={entry['o2']} o={entry['o']}")
    log("secondaries pull these entries and re-apply them; the entry is idempotent ($v:2 diff), not the original command")


def r2_write_concern(client: MongoClient) -> None:
    heading("R2", "Write concern: how much durability each acknowledgement buys, and what it costs")
    coll = client.stgrace_hospital.appointments
    rows = []
    for label, wc in [("w:0 (fire and forget)", WriteConcern(w=0)),
                      ("w:1 (primary memory)", WriteConcern(w=1)),
                      ("w:1, j:true (primary journal)", WriteConcern(w=1, j=True)),
                      ("w:majority (2 of 3)", WriteConcern(w="majority")),
                      ("w:3 (all members)", WriteConcern(w=3))]:
        c = coll.with_options(write_concern=wc)
        samples = []
        for i in range(200):
            s = time.perf_counter()
            c.insert_one({"kind": "wc-test", "mode": label, "i": i, "at": datetime.now()})
            samples.append((time.perf_counter() - s) * 1000)
        rows.append((label, statistics.median(samples), sorted(samples)[int(len(samples) * 0.95)]))
    for label, med, p95 in rows:
        log(f"  {label:<32} median {med:6.2f} ms   p95 {p95:6.2f} ms")
    coll.delete_many({"kind": "wc-test"})
    log("survives primary crash?  w:0 no | w:1 no (may be rolled back) | j:true not if the disk is lost | "
        "w:majority yes | w:3 yes, but blocks if any member is down")


def r3_lag_and_consistency(client: MongoClient) -> None:
    heading("R3", "Replication lag: pause member :27020 and watch consistency guarantees")
    lagger = direct(27020)
    lagger.admin.command("configureFailPoint", "stopReplProducer", mode="alwaysOn")
    log("fail point stopReplProducer ON at :27020 - it stops fetching the oplog (simulates a slow WAN link)")
    db = client.stgrace_hospital
    pid = 50000
    before = lagger.stgrace_hospital.patients.find_one({"_id": pid})["contact"]["phone"]
    db.patients.with_options(write_concern=WriteConcern(w=1)).update_one(
        {"_id": pid}, {"$set": {"contact.phone": "08039990000"}})
    log(f"primary: patient {pid} phone changed {before} -> 08039990000 (w:1)")
    time.sleep(1)
    for port in PORTS:
        v = direct(port).stgrace_hospital.patients.find_one({"_id": pid})["contact"]["phone"]
        log(f"  read from :{port} -> {v}{'   <-- STALE' if v != '08039990000' else ''}")

    try:
        db.patients.with_options(write_concern=WriteConcern(w="majority", wtimeout=3000)).update_one(
            {"_id": pid}, {"$set": {"next_of_kin.phone": "08031230000"}})
        log("w:majority write: acknowledged (primary + the healthy secondary = 2 of 3)")
    except WTimeoutError as e:
        log(f"w:majority write: TIMEOUT {e}")
    try:
        db.patients.with_options(write_concern=WriteConcern(w=3, wtimeout=3000)).update_one(
            {"_id": pid}, {"$set": {"next_of_kin.name": "DEMO"}})
        log("w:3 write: acknowledged")
    except WTimeoutError as e:
        log(f"w:3 write: wtimeout after 3 s - {e.details.get('errmsg', e)} "
            f"(the write IS applied on the primary; only the acknowledgement failed)")

    with client.start_session(causal_consistency=True) as writer:
        db.patients.update_one({"_id": pid}, {"$set": {"contact.email": "demo@causal.example"}}, session=writer)
        log("causal session: wrote a new e-mail address on the primary")
        with lagger.start_session(causal_consistency=True) as reader:
            reader.advance_cluster_time(writer.cluster_time)
            reader.advance_operation_time(writer.operation_time)
            try:
                lagger.stgrace_hospital.patients.find_one({"_id": pid}, session=reader, max_time_ms=3000)
                log("causal read on :27020 returned (unexpected)")
            except PyMongoError as e:
                log(f"causal read on lagging :27020 -> {type(e).__name__}: it WAITS for the write rather than "
                    f"return stale data, and gives up after maxTimeMS=3000")

    lagger.admin.command("configureFailPoint", "stopReplProducer", mode="off")
    log("fail point OFF; :27020 catches up")
    time.sleep(2)
    v = lagger.stgrace_hospital.patients.find_one({"_id": pid})["contact"]
    log(f"  read from :27020 -> phone {v['phone']}, email {v.get('email')}  (eventually consistent)")
    st = client.admin.command("replSetGetStatus")
    prim = next(m for m in st["members"] if m["stateStr"] == "PRIMARY")["optimeDate"]
    for m in st["members"]:
        log(f"  {m['name']:<16} {m['stateStr']:<9} lag {(prim - m['optimeDate']).total_seconds():.1f} s")


def r4_transactions(client: MongoClient) -> None:
    heading("R4", "Multi-document ACID transactions (refused by the standalone server in Portfolio 4, F6)")
    db = client.stgrace_hospital
    enc = db.encounters.find_one({"billing.status": "Unpaid"})
    pid, balance = enc["patient"]["patient_id"], enc["billing"]["balance"]
    neg = Decimal128(-balance.to_decimal())

    def show(tag):
        e = db.encounters.find_one({"_id": enc["_id"]}, {"billing": 1})["billing"]
        p = db.patients.find_one({"_id": pid}, {"stats.outstanding": 1})["stats"]["outstanding"]
        log(f"  {tag:<22} encounter billing.status={e['status']:<7} paid={e['paid']}  patient outstanding={p}")

    def pay(session):
        db.encounters.update_one({"_id": enc["_id"]},
                                 {"$set": {"billing.status": "Paid", "billing.paid": enc["billing"]["total"],
                                           "billing.balance": Decimal128("0.00")}}, session=session)
        db.patients.update_one({"_id": pid}, {"$inc": {"stats.outstanding": neg}}, session=session)

    show("before")
    log("transaction 1: pay the invoice, then fail before commit (e.g. the POS terminal declines)")
    with client.start_session() as s:
        try:
            with s.start_transaction(write_concern=WriteConcern("majority"), read_concern=ReadConcern("snapshot")):
                pay(s)
                raise RuntimeError("POS declined")
        except RuntimeError as e:
            log(f"  aborted: {e}")
    show("after abort")

    log("transaction 2: pay the invoice and commit")
    with client.start_session() as s:
        s.with_transaction(pay, write_concern=WriteConcern("majority"), read_concern=ReadConcern("snapshot"))
    show("after commit")

    log("write conflict: two cashiers' transactions update the same encounter")
    other = db.encounters.find_one({"billing.status": "Unpaid"})
    s1, s2 = client.start_session(), client.start_session()
    s1.start_transaction()
    s2.start_transaction()
    db.encounters.update_one({"_id": other["_id"]}, {"$set": {"billing.status": "Partial"}}, session=s1)
    log("  T1 updated the encounter (not yet committed)")
    try:
        db.encounters.update_one({"_id": other["_id"]}, {"$set": {"billing.status": "Paid"}}, session=s2)
    except OperationFailure as e:
        log(f"  T2 -> code {e.code} {e.details.get('codeName')}, labels {e.details.get('errorLabels')}: "
            f"no waiting, T2 is aborted immediately and should be retried")
        s2.abort_transaction()
    s1.abort_transaction()
    s1.end_session()
    s2.end_session()
    log("  (with_transaction() retries TransientTransactionError automatically)")


def r5_failover(client: MongoClient) -> None:
    heading("R5", "Automatic failover: kill the PRIMARY process while a front-desk client keeps booking")
    log(member_states())
    writer_client = MongoClient(URI, retryWrites=True, serverSelectionTimeoutMS=30000,
                                w="majority", heartbeatFrequencyMS=500)
    coll = writer_client.stgrace_hospital.appointments
    stop = threading.Event()
    results = []

    def front_desk():
        i = 0
        while not stop.is_set():
            i += 1
            s = time.perf_counter()
            try:
                coll.insert_one({"kind": "failover-test", "ticket": i, "at": datetime.now()})
                results.append((s, (time.perf_counter() - s) * 1000, True))
            except PyMongoError as e:
                results.append((s, (time.perf_counter() - s) * 1000, False))
                log(f"  front desk write {i} FAILED: {type(e).__name__}")
            time.sleep(0.05)

    t = threading.Thread(target=front_desk)
    t.start()
    time.sleep(3)
    old = primary_port()
    pid = direct(old).admin.command("serverStatus")["pid"]
    kill_at = time.perf_counter()
    os.kill(pid, signal.SIGTERM)                       # TerminateProcess on Windows: an abrupt crash
    log(f"KILLED primary :{old} (pid {pid})")
    new = None
    while new is None or new == old:
        time.sleep(0.2)
        new = primary_port()
    elected = time.perf_counter() - kill_at
    log(f"new PRIMARY :{new} elected {elected:.1f} s after the crash   [{member_states()}]")
    time.sleep(3)
    stop.set()
    t.join()
    ok = [r for r in results if r[2]]
    during = [r for r in results if r[0] >= kill_at - 0.1 and r[0] <= kill_at + elected]
    log(f"front desk: {len(results)} bookings attempted, {len(ok)} succeeded, {len(results) - len(ok)} failed")
    log(f"  normal latency median {statistics.median(r[1] for r in ok if r[0] < kill_at - 0.5):.1f} ms; "
        f"slowest booking {max(r[1] for r in results) / 1000:.1f} s (it waited for the election, then was retried)")
    log(f"  bookings in flight during the election: {len(during)}")
    stored = writer_client.stgrace_hospital.appointments.count_documents({"kind": "failover-test"})
    log(f"  documents stored: {stored} (= {len(ok)} acknowledged; w:majority writes are never lost on failover)")

    log(f"restarting the crashed member :{old}")
    restart_at = time.perf_counter()
    cluster.start_mongod(next(n for n in cluster.NODES["rs"] if n["port"] == old))
    cluster.wait_for(old)
    while True:
        h = direct(old).admin.command("hello")
        if h.get("secondary") or h.get("isWritablePrimary"):
            break
        time.sleep(0.5)
    log(f"  :{old} rejoined as SECONDARY after {time.perf_counter() - restart_at:.1f} s and caught up from the oplog")
    while primary_port() != old and time.perf_counter() - restart_at < 60:
        time.sleep(0.5)
    log(f"  :{old} has priority 2, so once caught up it called an election and took PRIMARY back "
        f"({time.perf_counter() - restart_at:.1f} s after restart)   [{member_states()}]")
    writer_client.stgrace_hospital.appointments.delete_many({"kind": "failover-test"})
    writer_client.close()


def r6_majority_loss(client: MongoClient) -> None:
    heading("R6", "Loss of majority: two members go down (e.g. the link to the server room is cut)")
    prim = primary_port()
    others = [p for p in PORTS if p != prim]
    for p in others:
        try:
            direct(p).admin.command("shutdown", force=True)
        except PyMongoError:
            pass
    down_at = time.perf_counter()
    log(f"stopped :{others[0]} and :{others[1]}; only :{prim} remains (1 of 3 votes)")
    while direct(prim).admin.command("hello").get("isWritablePrimary"):
        time.sleep(0.5)
    log(f":{prim} stepped down to SECONDARY after {time.perf_counter() - down_at:.1f} s - it can no longer "
        f"prove it is the only primary")
    c = MongoClient(URI, serverSelectionTimeoutMS=3000, w="majority")
    try:
        c.stgrace_hospital.appointments.insert_one({"kind": "partition-test"})
        log("write: accepted (unexpected)")
    except PyMongoError as e:
        log(f"write: REFUSED ({type(e).__name__}) - consistency is kept by giving up write availability")
    r = MongoClient(URI, serverSelectionTimeoutMS=3000, readPreference="secondaryPreferred")
    p = r.stgrace_hospital.patients.find_one({"_id": 1}, {"hospital_no": 1, "name": 1})
    log(f"read with readPreference=secondaryPreferred: {p}  - clinicians can still look up records "
        f"(possibly stale)")
    log("restarting the two members")
    back_at = time.perf_counter()
    for p in others:
        cluster.start_mongod(next(n for n in cluster.NODES["rs"] if n["port"] == p))
    while primary_port() is None:
        time.sleep(0.5)
    log(f"majority restored; PRIMARY available again after {time.perf_counter() - back_at:.1f} s   [{member_states()}]")


def main() -> None:
    client = MongoClient(URI, serverSelectionTimeoutMS=20000)
    log(f"MongoDB {client.server_info()['version']}  {member_states()}")
    r0_topology(client)
    r1_load_and_replicate(client)
    r2_write_concern(client)
    r3_lag_and_consistency(client)
    r4_transactions(client)
    r5_failover(client)
    r6_majority_loss(client)
    print()
    log(f"final state: {member_states()}")


if __name__ == "__main__":
    main()
