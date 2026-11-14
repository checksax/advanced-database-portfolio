"""
Portfolio 4: query workload on the MongoDB model, with evidence.

  A. Storage profile of each collection
  B. Sample documents exported to samples/*.json
  C. Ten queries (M1-M10). For each: wall-clock median, explain() stats, and
     - for M1-M3, the same question asked of PostgreSQL (Portfolio 2 query),
       with a check that both databases return the same answer
     - for M1-M3, the same query forced to a collection scan, to show what
       the index is worth

Usage:  python queries.py      (writes results/ and plans/)
"""

import json
import statistics
import time
from datetime import timedelta
from pathlib import Path

import psycopg
from bson import json_util
from pymongo import MongoClient

from common import AS_OF, MONGO_DB, MONGO_URI, PG_DSN

HERE = Path(__file__).resolve().parent
RESULTS, PLANS, SAMPLES = HERE / "results", HERE / "plans", HERE / "samples"
RUNS = 7
CI = {"locale": "en", "strength": 2}           # case-insensitive collation

db = MongoClient(MONGO_URI)[MONGO_DB]
out_lines: list[str] = []


def emit(text: str = "") -> None:
    print(text)
    out_lines.append(text)


def timed(fn) -> tuple[float, object]:
    result = fn()                               # warm-up
    samples = []
    for _ in range(RUNS):
        start = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), result


def walk(node, key, found):
    """Collect every value of `key` anywhere in a nested explain document."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            walk(v, key, found)
    elif isinstance(node, list):
        for v in node:
            walk(v, key, found)
    return found


def explain(name: str, command: dict) -> dict:
    plan = db.command("explain", command, verbosity="executionStats")
    (PLANS / f"{name}.json").write_text(json_util.dumps(plan, indent=1), encoding="utf-8")
    winning = walk(plan, "winningPlan", [])
    stages = []
    for w in winning[:1]:
        stages = [s for s in walk(w, "stage", []) if isinstance(s, str)]
    index_names = sorted({i for i in walk(winning[:1], "indexName", []) if isinstance(i, str)})
    server_ms = [v for v in walk(plan, "executionTimeMillis", []) if isinstance(v, (int, float))]
    return {"server_ms": max(server_ms) if server_ms else None,
            "docs": sum(v for v in walk(plan, "totalDocsExamined", []) if isinstance(v, int)),
            "keys": sum(v for v in walk(plan, "totalKeysExamined", []) if isinstance(v, int)),
            "stages": " > ".join(dict.fromkeys(reversed(stages))), "indexes": ", ".join(index_names) or "-"}


def find_cmd(coll, flt, sort=None, projection=None, hint=None, collation=None):
    c = {"find": coll, "filter": flt}
    if sort:
        c["sort"] = sort
    if projection:
        c["projection"] = projection
    if hint:
        c["hint"] = hint
    if collation:
        c["collation"] = collation
    return c


def agg_cmd(coll, pipeline, hint=None):
    c = {"aggregate": coll, "pipeline": pipeline, "cursor": {}}
    if hint:
        c["hint"] = hint
    return c


def pg_timed(sql: str):
    with psycopg.connect(PG_DSN) as pg:
        cur = pg.cursor()
        cur.execute("SET jit = off")
        return timed(lambda: cur.execute(sql).fetchall())


# ---------------------------------------------------------------------------
# A. Storage profile
# ---------------------------------------------------------------------------
def storage_profile() -> list[str]:
    rows = ["| Collection | Model | Documents | Avg doc size | Data size | Storage (compressed) | Indexes |",
            "|---|---|---:|---:|---:|---:|---:|"]
    models = {"patients": "document", "encounters": "document", "drugs": "document (attribute pattern)",
              "staff": "document", "referrals": "graph edges", "vitals": "time series",
              "cache": "key-value (TTL)", "queue": "key-value / queue"}
    for name in models:
        s = next(db[name].aggregate([{"$collStats": {"storageStats": {}}}]))["storageStats"]
        count = db[name].estimated_document_count()
        rows.append(f"| `{name}` | {models[name]} | {count:,} | {s.get('avgObjSize', 0):,} B | "
                    f"{s.get('size', 0) / 1048576:,.1f} MB | {s.get('storageSize', 0) / 1048576:,.1f} MB | "
                    f"{s.get('totalIndexSize', 0) / 1048576:,.1f} MB |")
    return rows


# ---------------------------------------------------------------------------
# B. Samples
# ---------------------------------------------------------------------------
def export_samples() -> None:
    SAMPLES.mkdir(exist_ok=True)
    picks = {
        "patient": db.patients.find_one({"_id": 1}),
        "patient_with_allergies_and_insurance": db.patients.find_one({"allergies": {"$exists": True},
                                                                      "insurance": {"$exists": True}}),
        "encounter_outpatient_malaria": db.encounters.find_one({"type": "Outpatient", "diagnoses.code": "B54",
                                                                "labs.result.species": {"$exists": True},
                                                                "prescription": {"$exists": True}}),
        "encounter_inpatient_with_culture": db.encounters.find_one({"type": "Inpatient",
                                                                    "labs.result.culture": {"$exists": True}}),
        "encounter_with_fbc_panel": db.encounters.find_one({"labs.result.panel": {"$exists": True}}),
        "drug_with_attributes": db.drugs.find_one({"generic_name": "Insulin (Soluble)"}),
        "staff_doctor": db.staff.find_one({"role": "Doctor"}),
        "referral_edge": db.referrals.find_one({"from_department": "General Medicine", "to_department": "Cardiology"}),
        "vitals_reading": db.vitals.find_one({}, sort=[("ts", -1)]),
    }
    for name, doc in picks.items():
        (SAMPLES / f"{name}.json").write_text(json_util.dumps(doc, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS),
                                              encoding="utf-8")


# ---------------------------------------------------------------------------
# C. Queries
# ---------------------------------------------------------------------------
SQL_Q3 = """SELECT COUNT(DISTINCT e.encounter_id) FROM hospital.patients p
            JOIN hospital.encounters e ON e.patient_id = p.patient_id
            JOIN hospital.staff s ON s.staff_id = e.doctor_id
            LEFT JOIN hospital.diagnoses d ON d.encounter_id = e.encounter_id
            LEFT JOIN hospital.icd10_codes c ON c.code = d.icd10_code
            WHERE p.hospital_no = 'SGH-0000001'"""
SQL_Q3_FULL = """SELECT e.encounter_date, e.encounter_type, s.last_name, d.icd10_code, c.description, d.diagnosis_type
            FROM hospital.patients p
            JOIN hospital.encounters e ON e.patient_id = p.patient_id
            JOIN hospital.staff s ON s.staff_id = e.doctor_id
            LEFT JOIN hospital.diagnoses d ON d.encounter_id = e.encounter_id
            LEFT JOIN hospital.icd10_codes c ON c.code = d.icd10_code
            WHERE p.hospital_no = 'SGH-0000001' ORDER BY e.encounter_date DESC"""
SQL_Q7 = """SELECT date_trunc('week', e.encounter_date) AS week, COUNT(*)
            FROM hospital.diagnoses d JOIN hospital.encounters e ON e.encounter_id = d.encounter_id
            WHERE d.icd10_code IN ('B54', 'B50.9')
              AND e.encounter_date >= '2026-08-04' AND e.encounter_date < '2026-11-03'
            GROUP BY 1 ORDER BY 1"""
SQL_Q8 = """SELECT r.resulted_at, t.name, p.hospital_no, p.phone
            FROM hospital.lab_results r
            JOIN hospital.lab_orders lo ON lo.lab_order_id = r.lab_order_id
            JOIN hospital.lab_tests t ON t.test_id = lo.test_id
            JOIN hospital.encounters e ON e.encounter_id = lo.encounter_id
            JOIN hospital.patients p ON p.patient_id = e.patient_id
            WHERE r.flag = 'Critical' AND r.resulted_at >= '2026-10-27'
            ORDER BY r.resulted_at DESC"""

week_start = AS_OF - timedelta(days=91)


def queries() -> list[dict]:
    from datetime import datetime
    since_7d = datetime(2026, 10, 27)
    return [
        {
            "id": "M1", "title": "Patient clinical history (= SQL Q3)", "coll": "encounters", "kind": "find",
            "filter": {"patient.patient_id": 1},
            "sort": {"date": -1},
            "projection": {"date": 1, "type": 1, "doctor.name": 1, "diagnoses": 1},
            "sql": SQL_Q3_FULL, "compare": "encounters",
        },
        {
            "id": "M2", "title": "Weekly malaria cases, last 13 weeks (= SQL Q7)", "coll": "encounters", "kind": "agg",
            "pipeline": [
                {"$match": {"diagnoses.code": {"$in": ["B54", "B50.9"]},
                            "date": {"$gte": datetime(2026, 8, 4), "$lt": datetime(2026, 11, 3)}}},
                {"$unwind": "$diagnoses"},
                {"$match": {"diagnoses.code": {"$in": ["B54", "B50.9"]}}},
                {"$group": {"_id": {"$dateTrunc": {"date": "$date", "unit": "week", "startOfWeek": "monday"}},
                            "cases": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ],
            "sql": SQL_Q7, "compare": "weekly",
        },
        {
            "id": "M3", "title": "Critical lab results in last 7 days + patient phone (= SQL Q8)", "coll": "encounters",
            "kind": "agg",
            "pipeline": [
                {"$match": {"labs.result.flag": "Critical", "labs.result.resulted_at": {"$gte": since_7d}}},
                {"$unwind": "$labs"},
                {"$match": {"labs.result.flag": "Critical", "labs.result.resulted_at": {"$gte": since_7d}}},
                {"$lookup": {"from": "patients", "localField": "patient.patient_id", "foreignField": "_id",
                             "as": "p", "pipeline": [{"$project": {"contact.phone": 1}}]}},
                {"$project": {"_id": 0, "resulted_at": "$labs.result.resulted_at", "test": "$labs.test",
                              "hospital_no": "$patient.hospital_no", "phone": {"$first": "$p.contact.phone"}}},
                {"$sort": {"resulted_at": -1}},
            ],
            "sql": SQL_Q8, "compare": "count",
        },
        {
            "id": "M3b", "title": "M3 without the $lookup (as if phone were embedded in encounter.patient)",
            "coll": "encounters", "kind": "agg",
            "pipeline": [
                {"$match": {"labs.result.flag": "Critical", "labs.result.resulted_at": {"$gte": since_7d}}},
                {"$unwind": "$labs"},
                {"$match": {"labs.result.flag": "Critical", "labs.result.resulted_at": {"$gte": since_7d}}},
                {"$project": {"_id": 0, "resulted_at": "$labs.result.resulted_at", "test": "$labs.test",
                              "hospital_no": "$patient.hospital_no"}},
                {"$sort": {"resulted_at": -1}},
            ],
        },
        {
            "id": "M4", "title": "Case-insensitive patient search via collation index (cf. SQL Q2)", "coll": "patients",
            "kind": "find", "collation": CI,
            "filter": {"name.last": "okafor", "name.first": {"$gte": "ng", "$lt": "nh"}},
            "sort": {"date_of_birth": 1},
            "projection": {"hospital_no": 1, "name": 1, "date_of_birth": 1},
        },
        {
            "id": "M5", "title": "Revenue collected by department and visit month, last 12 months", "coll": "encounters",
            "kind": "agg",
            "pipeline": [
                {"$match": {"date": {"$gte": datetime(2025, 11, 1), "$lt": datetime(2026, 11, 1)},
                            "billing": {"$exists": True}}},
                {"$group": {"_id": {"month": {"$dateTrunc": {"date": "$date", "unit": "month"}},
                                    "department": "$doctor.department"},
                            "collected": {"$sum": "$billing.paid"}, "visits": {"$sum": 1}}},
                {"$sort": {"_id.month": 1, "_id.department": 1}},
            ],
        },
        {
            "id": "M6", "title": "Top 10 drugs dispensed, last 90 days ($unwind of embedded items)", "coll": "encounters",
            "kind": "agg",
            "pipeline": [
                {"$match": {"date": {"$gte": AS_OF - timedelta(days=90)}, "prescription.status": "Dispensed"}},
                {"$unwind": "$prescription.items"},
                {"$group": {"_id": "$prescription.items.drug", "units": {"$sum": "$prescription.items.dispensed"},
                            "prescriptions": {"$sum": 1}}},
                {"$sort": {"units": -1}}, {"$limit": 10},
            ],
        },
        {
            "id": "M7", "title": "Antibiotic resistance from polymorphic culture results", "coll": "encounters",
            "kind": "agg",
            "pipeline": [
                {"$match": {"labs.result.culture": {"$exists": True}}},
                {"$unwind": "$labs"},
                {"$match": {"labs.result.culture": {"$exists": True}}},
                {"$unwind": "$labs.result.culture.sensitivities"},
                {"$group": {"_id": {"organism": "$labs.result.culture.organism",
                                    "antibiotic": "$labs.result.culture.sensitivities.antibiotic"},
                            "tested": {"$sum": 1},
                            "resistant": {"$sum": {"$cond": [{"$eq": ["$labs.result.culture.sensitivities.result", "R"]}, 1, 0]}}}},
                {"$project": {"tested": 1, "resistant": 1,
                              "resistance_pct": {"$round": [{"$multiply": [{"$divide": ["$resistant", "$tested"]}, 100]}, 1]}}},
                {"$sort": {"_id.organism": 1, "resistance_pct": -1}},
            ],
        },
        {
            "id": "M8", "title": "Time series: inpatients whose 12-hour moving-average temperature is above 38 C now",
            "coll": "vitals", "kind": "agg",
            "pipeline": [
                {"$match": {"ts": {"$gte": AS_OF - timedelta(days=3)}}},
                {"$setWindowFields": {"partitionBy": "$meta.admission_id", "sortBy": {"ts": 1},
                                      "output": {"temp_ma": {"$avg": "$temperature_c",
                                                             "window": {"range": [-12, 0], "unit": "hour"}},
                                                 "latest": {"$last": "$ts", "window": {"documents": ["unbounded", "unbounded"]}}}}},
                {"$match": {"$expr": {"$and": [{"$eq": ["$ts", "$latest"]}, {"$gt": ["$temp_ma", 38]}]}}},
                {"$project": {"_id": 0, "admission_id": "$meta.admission_id", "ward": "$meta.ward", "ts": 1,
                              "temperature_c": 1, "temp_ma": {"$round": ["$temp_ma", 2]}}},
                {"$sort": {"temp_ma": -1}},
            ],
        },
        {
            "id": "M9", "title": "Graph: departments reachable through referral chains from General Medicine doctor",
            "coll": "staff", "kind": "agg",
            "pipeline": [
                {"$match": {"_id": None}},                      # filled in at run time
                {"$graphLookup": {"from": "referrals", "startWith": "$_id", "connectFromField": "to_doctor",
                                  "connectToField": "from_doctor", "as": "chain", "maxDepth": 2, "depthField": "hops"}},
                {"$unwind": "$chain"},
                {"$group": {"_id": {"hops": "$chain.hops", "department": "$chain.to_department"},
                            "referrals": {"$sum": 1}}},
                {"$sort": {"_id.hops": 1, "referrals": -1}},
            ],
        },
        {
            "id": "M10", "title": "Key-value: read a cached patient summary by key", "coll": "cache", "kind": "find",
            "filter": {"_id": "patient:summary:1"},
        },
    ]


def run_query(q: dict):
    coll = db[q["coll"]]
    if q["kind"] == "find":
        cursor = lambda: list(coll.find(q["filter"], q.get("projection"), sort=list(q["sort"].items()) if q.get("sort") else None,
                                        collation=q.get("collation")))
        cmd = find_cmd(q["coll"], q["filter"], q.get("sort"), q.get("projection"), collation=q.get("collation"))
        scan = find_cmd(q["coll"], q["filter"], q.get("sort"), q.get("projection"), hint={"$natural": 1},
                        collation=q.get("collation"))
        scan_fn = lambda: list(coll.find(q["filter"], q.get("projection"), hint=[("$natural", 1)],
                                         collation=q.get("collation")))
    else:
        cursor = lambda: list(coll.aggregate(q["pipeline"]))
        cmd = agg_cmd(q["coll"], q["pipeline"])
        scan = agg_cmd(q["coll"], q["pipeline"], hint={"$natural": 1})
        scan_fn = lambda: list(coll.aggregate(q["pipeline"], hint={"$natural": 1}))
    return cursor, cmd, scan, scan_fn


def main() -> None:
    for d in (RESULTS, PLANS, SAMPLES):
        d.mkdir(exist_ok=True)

    # cache entry for M10 (key-value read); the TTL demo is in features_demo.py
    p1 = db.patients.find_one({"_id": 1}, {"hospital_no": 1, "name": 1, "stats": 1, "allergies": 1})
    db.cache.replace_one({"_id": "patient:summary:1"},
                         {"_id": "patient:summary:1", "value": p1, "expires_at": AS_OF + timedelta(days=3650)},
                         upsert=True)

    emit("# Portfolio 4 query results")
    emit()
    emit(f"MongoDB {db.client.server_info()['version']}, wall-clock median of {RUNS} warm runs.")
    emit()
    emit("## Storage profile")
    emit()
    for row in storage_profile():
        emit(row)

    export_samples()

    summary = ["| Query | Question | Docs returned | Client time | Server time | Keys examined | Docs examined | Plan | Index |",
               "|---|---|---:|---:|---:|---:|---:|---|---|"]
    versus = ["| Question | MongoDB (indexed) | MongoDB (forced COLLSCAN) | PostgreSQL (Portfolio 2) | Same answer? |",
              "|---|---:|---:|---:|:---:|"]
    detail: list[str] = []

    gm_doctor = db.referrals.aggregate([{"$match": {"from_department": "General Medicine"}},
                                        {"$group": {"_id": "$from_doctor", "n": {"$sum": 1}}},
                                        {"$sort": {"n": -1}}, {"$limit": 1}]).next()["_id"]

    for q in queries():
        if q["id"] == "M9":
            q["pipeline"][0]["$match"]["_id"] = gm_doctor
        run, cmd, scan_cmd, scan_fn = run_query(q)
        ms, result = timed(run)
        stats = explain(q["id"], cmd)
        summary.append(f"| {q['id']} | {q['title']} | {len(result):,} | {ms:.2f} ms | {stats['server_ms']} ms | {stats['keys']:,} | "
                       f"{stats['docs']:,} | {stats['stages']} | {stats['indexes']} |")
        detail.append(f"\n### {q['id']} {q['title']}\n")
        detail.append("```")
        for doc in result[:8]:
            detail.append(json_util.dumps(doc, json_options=json_util.RELAXED_JSON_OPTIONS)[:240])
        if len(result) > 8:
            detail.append(f"... {len(result) - 8} more")
        detail.append("```")

        if "sql" in q:
            scan_ms, _ = timed(scan_fn)
            scan_stats = explain(f"{q['id']}_collscan", scan_cmd)
            sql_ms, sql_rows = pg_timed(q["sql"])
            if q["compare"] == "encounters":
                same = len(result) == pg_timed(SQL_Q3)[1][0][0]
            elif q["compare"] == "weekly":
                same = [(r["_id"], r["cases"]) for r in result] == [(w, c) for w, c in sql_rows]
            else:
                same = len(result) == len(sql_rows)
            versus.append(f"| {q['id']} {q['title'].split(' (')[0]} | {ms:.2f} ms ({stats['docs']:,} docs) | "
                          f"{scan_ms:.2f} ms ({scan_stats['docs']:,} docs) | {sql_ms:.2f} ms | {'yes' if same else '**NO**'} |")

    emit()
    emit("## Queries")
    emit()
    for row in summary:
        emit(row)
    emit()
    emit("## MongoDB vs PostgreSQL on the same questions")
    emit()
    for row in versus:
        emit(row)
    emit()
    emit("## Query output (first rows)")
    for line in detail:
        emit(line)
    (RESULTS / "query_results.md").write_text("\n".join(out_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
