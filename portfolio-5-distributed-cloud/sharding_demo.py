"""
Portfolio 5: horizontal scaling with a sharded cluster.

Requires:  python cluster.py start shard
           (config server replica set + shards shardA, shardB + mongos router)

  S0  Cluster topology
  S1  Hashed shard key: encounters spread evenly by patient
  S2  Query routing: targeted (shard key in the filter) vs scatter-gather
  S3  Zone sharding: each branch's patients live on that branch's shard
  S4  Range shard key on a date: the "hot shard" problem, compared with hashed
  S5  A shard fails: what still works

Usage:  python sharding_demo.py > results/sharding_demo_output.txt
"""

import statistics
import time
from datetime import datetime, timedelta

from bson.max_key import MaxKey
from bson.min_key import MinKey
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import cluster

MONGOS = MongoClient("127.0.0.1", 27040, serverSelectionTimeoutMS=5000)
SOURCE = MongoClient("mongodb://localhost:27017")["stgrace_hospital"]
DB = "stgrace_hospital"
db = MONGOS[DB]
T0 = time.perf_counter()

SOUTH = {"Lagos", "Oyo", "Ogun", "Osun", "Ondo", "Ekiti", "Enugu", "Anambra", "Imo", "Abia", "Ebonyi",
         "Rivers", "Delta", "Edo", "Bayelsa", "Cross-River", "Akwa-Ibom"}


def log(text: str = "") -> None:
    print(f"[{time.perf_counter() - T0:7.2f}s] {text}" if text else "", flush=True)


def heading(code: str, title: str) -> None:
    print()
    print("=" * 96)
    print(f"{code}  {title}")
    print("=" * 96, flush=True)


def per_shard(coll: str) -> dict:
    return {d["shard"]: d["count"] for d in db[coll].aggregate([{"$collStats": {"count": {}}}])}


def shards_used(explain: dict) -> list[str]:
    qp = explain.get("queryPlanner", {})
    if "winningPlan" in qp and "shards" in qp["winningPlan"]:
        return [s["shardName"] for s in qp["winningPlan"]["shards"]]
    if "shards" in explain:
        return sorted(explain["shards"].keys())
    return []


def timed(fn, runs=7) -> float:
    fn()
    samples = []
    for _ in range(runs):
        s = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - s) * 1000)
    return statistics.median(samples)


def s0_topology() -> None:
    heading("S0", "Sharded cluster topology")
    for s in MONGOS.admin.command("listShards")["shards"]:
        log(f"  shard {s['_id']:<7} host {s['host']}  state {s.get('state')}")
    log(f"  config server replica set 'cfg' (metadata: which chunk lives where), router mongos :27040")
    log(f"  balancer running: {MONGOS.admin.command('balancerStatus')['mode']}")
    for coll in ("encounters", "patients", "appt_range", "appt_hashed"):
        db.drop_collection(coll)


def s1_hashed() -> None:
    heading("S1", "Hashed shard key: encounters sharded on hash(patient.patient_id)")
    MONGOS.admin.command("shardCollection", f"{DB}.encounters", key={"patient.patient_id": "hashed"})
    log("shardCollection encounters key {patient.patient_id: 'hashed'}")
    docs = list(SOURCE.encounters.find({"date": {"$gte": datetime(2026, 5, 1)}}))
    start = time.time()
    for i in range(0, len(docs), 5000):
        db.encounters.insert_many(docs[i:i + 5000], ordered=False)
    db.encounters.create_index([("patient.patient_id", 1), ("date", -1)])
    db.encounters.create_index("patient.hospital_no")
    log(f"inserted {len(docs):,} encounters through mongos in {time.time() - start:.1f}s")
    dist = per_shard("encounters")
    total = sum(dist.values())
    for shard, n in sorted(dist.items()):
        log(f"  {shard}: {n:>6,} documents ({100 * n / total:.1f}%)")
    chunks = list(MONGOS.config.chunks.aggregate([
        {"$lookup": {"from": "collections", "localField": "uuid", "foreignField": "uuid", "as": "c"}},
        {"$match": {"c._id": f"{DB}.encounters"}}, {"$group": {"_id": "$shard", "chunks": {"$sum": 1}}}]))
    log(f"  chunks per shard (config.chunks): {({c['_id']: c['chunks'] for c in chunks})}")
    log("hashing spreads patients pseudo-randomly, so data and write load are balanced, and all of one "
        "patient's visits stay together on one shard")


def s2_routing() -> None:
    heading("S2", "Query routing by mongos")
    cases = [
        ("history by patient_id (shard key)", {"patient.patient_id": 1}),
        ("history by hospital_no (not the shard key)", {"patient.hospital_no": "SGH-0000001"}),
    ]
    for label, flt in cases:
        exp = db.command("explain", {"find": "encounters", "filter": flt, "sort": {"date": -1}}, verbosity="queryPlanner")
        stage = exp["queryPlanner"]["winningPlan"]["stage"]
        ms = timed(lambda: list(db.encounters.find(flt).sort("date", -1)))
        log(f"  {label:<44} {stage:<12} shards {shards_used(exp)}  {ms:6.2f} ms  "
            f"{db.encounters.count_documents(flt)} docs")
    pipeline = [{"$match": {"diagnoses.code": {"$in": ["B54", "B50.9"]}, "date": {"$gte": datetime(2026, 8, 4)}}},
                {"$group": {"_id": {"$dateTrunc": {"date": "$date", "unit": "week"}}, "cases": {"$sum": 1}}},
                {"$sort": {"_id": 1}}]
    exp = db.command("explain", {"aggregate": "encounters", "pipeline": pipeline, "cursor": {}}, verbosity="queryPlanner")
    ms = timed(lambda: list(db.encounters.aggregate(pipeline)))
    log(f"  {'weekly malaria aggregation (no shard key)':<44} {exp.get('mergeType', '?'):<12} "
        f"shards {shards_used(exp)}  {ms:6.2f} ms")
    split = exp.get("splitPipeline") or {}
    log(f"    shard part: {[list(s)[0] for s in split.get('shardsPart', [])]}  "
        f"merge part: {[list(s)[0] for s in split.get('mergerPart', [])]}")
    log("targeted queries touch one shard; anything without the shard key is scatter-gather: every shard "
        "works and mongos merges, so cost grows with the number of shards")


def s3_zones() -> None:
    heading("S3", "Zone sharding: St. Grace opens an Abuja branch; each branch's patients stay on its own shard")
    MONGOS.admin.command("addShardToZone", "shardA", zone="LAGOS")
    MONGOS.admin.command("addShardToZone", "shardB", zone="ABUJA")
    ns = f"{DB}.patients"
    for t in MONGOS.config.tags.find({"ns": ns}):                   # ranges left from an earlier run
        MONGOS.admin.command("updateZoneKeyRange", ns, min=t["min"], max=t["max"], zone=None)
    # The two zones tile the whole key space. Ending ABUJA at {LAGOS, MinKey} (not {ABUJA, MaxKey})
    # matters: a chunk boundary at {ABUJA, MaxKey} is the inclusive start of the next chunk, so a
    # query on branch alone would be routed to that neighbouring shard as well.
    MONGOS.admin.command("updateZoneKeyRange", ns, min={"branch": MinKey(), "_id": MinKey()},
                         max={"branch": "LAGOS", "_id": MinKey()}, zone="ABUJA")
    MONGOS.admin.command("updateZoneKeyRange", ns, min={"branch": "LAGOS", "_id": MinKey()},
                         max={"branch": MaxKey(), "_id": MaxKey()}, zone="LAGOS")
    log("zones: shardB = ABUJA [MinKey .. {LAGOS, MinKey}), shardA = LAGOS [{LAGOS, MinKey} .. MaxKey)")
    MONGOS.admin.command("shardCollection", ns, key={"branch": 1, "_id": 1})
    log("shardCollection patients key {branch: 1, _id: 1}  (compound: branch first, then patient id)")
    docs = []
    for p in SOURCE.patients.find({}, {"hospital_no": 1, "name": 1, "gender": 1, "contact": 1, "date_of_birth": 1}):
        p["branch"] = "LAGOS" if p.get("contact", {}).get("state_of_origin") in SOUTH else "ABUJA"
        docs.append(p)
    for i in range(0, len(docs), 5000):
        db.patients.insert_many(docs[i:i + 5000], ordered=False)
    branches = {d["_id"]: d["n"] for d in db.patients.aggregate([{"$group": {"_id": "$branch", "n": {"$sum": 1}}}])}
    log(f"inserted {len(docs):,} patients: {branches}")
    for shard, n in sorted(per_shard("patients").items()):
        log(f"  {shard}: {n:,} patients")
    for flt in ({"branch": "ABUJA"}, {"branch": "LAGOS", "_id": 2}, {"hospital_no": "SGH-0000002"}):
        exp = db.command("explain", {"find": "patients", "filter": flt}, verbosity="queryPlanner")
        log(f"  query {str(flt):<40} -> {exp['queryPlanner']['winningPlan']['stage']:<12} on {shards_used(exp)}")
    log("use: data residency and locality - each branch's server holds and serves its own patients; "
        "head-office reports still see all patients through mongos")


def s4_hotspot() -> None:
    heading("S4", "Range shard key on a timestamp vs hashed: where do new bookings go?")
    MONGOS.admin.command("balancerStop")
    log("balancer stopped so chunk placement stays as configured")
    cut = datetime(2026, 11, 1)
    MONGOS.admin.command("shardCollection", f"{DB}.appt_range", key={"scheduled_start": 1})
    MONGOS.admin.command("split", f"{DB}.appt_range", middle={"scheduled_start": cut})
    MONGOS.admin.command("moveRange", f"{DB}.appt_range", min={"scheduled_start": cut}, toShard="shardB")
    log(f"appt_range: range key scheduled_start; chunks [MinKey, {cut:%Y-%m-%d}) on shardA, [{cut:%Y-%m-%d}, MaxKey) on shardB")
    MONGOS.admin.command("shardCollection", f"{DB}.appt_hashed", key={"scheduled_start": "hashed"})
    log("appt_hashed: same data, hashed key on scheduled_start")
    new = [{"scheduled_start": datetime(2026, 11, 4, 8) + timedelta(minutes=10 * i), "doctor_id": 23, "patient_id": i}
           for i in range(2000)]
    db.appt_range.insert_many([dict(d) for d in new])
    db.appt_hashed.insert_many([dict(d) for d in new])
    log("inserted the same 2,000 new (future) appointments into both")
    log(f"  range key  : {dict(sorted(per_shard('appt_range').items()))}  <- every new booking hits ONE shard")
    log(f"  hashed key : {dict(sorted(per_shard('appt_hashed').items()))}")
    exp = db.command("explain", {"find": "appt_range",
                                 "filter": {"scheduled_start": {"$gte": datetime(2026, 11, 4), "$lt": datetime(2026, 11, 5)}}},
                     verbosity="queryPlanner")
    exp_h = db.command("explain", {"find": "appt_hashed",
                                   "filter": {"scheduled_start": {"$gte": datetime(2026, 11, 4), "$lt": datetime(2026, 11, 5)}}},
                       verbosity="queryPlanner")
    log(f"  'all bookings on 4 Nov' -> range key: {shards_used(exp)};  hashed key: {shards_used(exp_h)}")
    log("trade-off: a monotonically increasing range key makes date-range queries targeted but sends all "
        "inserts to the last chunk (hot shard); hashing spreads inserts but turns range queries into scatter-gather")
    MONGOS.admin.command("balancerStart")


def s5_shard_failure() -> None:
    heading("S5", "A shard goes down (shardB)")
    # find one patient whose data is on shardA by asking explain where it would route
    patient_a = None
    for doc in db.encounters.find({}, {"patient.patient_id": 1}).limit(200):
        pid = doc["patient"]["patient_id"]
        exp = db.command("explain", {"find": "encounters", "filter": {"patient.patient_id": pid}}, verbosity="queryPlanner")
        if shards_used(exp) == ["shardA"]:
            patient_a = pid
            break
    try:
        cluster.node(27032).admin.command("shutdown", force=True)
    except PyMongoError:
        pass
    time.sleep(2)
    log("shardB stopped")
    fast = MongoClient("127.0.0.1", 27040, serverSelectionTimeoutMS=5000)[DB]
    n = len(list(fast.encounters.find({"patient.patient_id": patient_a}).max_time_ms(5000)))
    log(f"  targeted query for patient {patient_a} (lives on shardA): OK, {n} encounters")
    try:
        list(fast.encounters.aggregate([{"$count": "n"}], maxTimeMS=8000))
        log("  scatter-gather count: OK (unexpected)")
    except PyMongoError as e:
        log(f"  scatter-gather count: FAILED ({type(e).__name__}) - needs every shard")
    partial = list(fast.encounters.find({}, {"_id": 1}, allow_partial_results=True, batch_size=100000)
                   .max_time_ms(8000))
    log(f"  find with allowPartialResults: {len(partial):,} of {sum(per_shard_cached.values()):,} documents "
        f"(only shardA's share) - degraded but available")
    cluster.start_mongod(next(n for n in cluster.NODES["shard"] if n["name"] == "shardB"))
    cluster.wait_for(27032)
    cluster.wait_primary(27032)
    time.sleep(2)
    log(f"shardB restarted; full count again: {db.encounters.count_documents({}):,}")
    log("each shard here is a 1-member replica set to save memory; in production every shard is a 3-member "
        "replica set, so a single server failure would trigger an election instead of an outage")


per_shard_cached: dict = {}


def main() -> None:
    log(f"mongos {MONGOS.server_info()['version']}")
    s0_topology()
    s1_hashed()
    per_shard_cached.update(per_shard("encounters"))
    s2_routing()
    s3_zones()
    s4_hotspot()
    s5_shard_failure()


if __name__ == "__main__":
    main()
