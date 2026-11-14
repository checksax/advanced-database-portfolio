"""
Portfolio 4: document-database behaviour demonstrated on the hospital model.

  F1  Schema validation: invalid documents are rejected with reasons
  F2  Schema evolution: a new optional field needs no migration
  F3  Polymorphism: different lab-result shapes in one collection
  F4  Single-document atomicity: dispensing updates several embedded items at once
  F5  Concurrency without transactions: 20 parallel conditional decrements, no lost update
  F6  Multi-document transactions on this standalone server (expected to fail)
  F7  Key-value cache with TTL expiry
  F8  Atomic work queue: two nurses calling patients, nobody called twice

All demo documents are removed at the end.

Usage:  python features_demo.py > results/features_demo_output.txt
"""

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from bson import json_util
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import OperationFailure, WriteError

from common import MONGO_DB, MONGO_URI

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]


def heading(code: str, title: str) -> None:
    print()
    print("=" * 90)
    print(f"{code}  {title}")
    print("=" * 90)


def show(label: str, value) -> None:
    text = value if isinstance(value, str) else json_util.dumps(value, json_options=json_util.RELAXED_JSON_OPTIONS)
    print(f"  {label}: {text}")


def first_reason(node, path=""):
    """Dig through MongoDB's nested validation details to the first human-readable reason."""
    if isinstance(node, dict):
        if "reason" in node:
            return f"{path}{node['reason']} (value {node.get('consideredValue')!r})" if "consideredValue" in node \
                else f"{path}{node['reason']}"
        for k, v in node.items():
            found = first_reason(v, f"{path}{node['propertyName']}: " if k == "details" and "propertyName" in node else path)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = first_reason(v, path)
            if found:
                return found
    return None


def f1_validation() -> None:
    heading("F1", "Schema validation ($jsonSchema): flexible, but not schemaless")
    bad_patients = [
        ("gender 'X' and a malformed phone",
         {"hospital_no": "SGH-9999901", "name": {"first": "Test", "last": "Patient"},
          "date_of_birth": datetime(1990, 1, 1), "gender": "X", "contact": {"phone": "12345"},
          "registered_at": datetime.now()}),
        ("missing date_of_birth, hospital number in the wrong format",
         {"hospital_no": "9999902", "name": {"first": "Test", "last": "Patient"}, "gender": "F",
          "contact": {"phone": "08031234567"}, "registered_at": datetime.now()}),
    ]
    for label, doc in bad_patients:
        try:
            db.patients.insert_one(doc)
            show(label, "ACCEPTED (unexpected)")
        except WriteError as e:
            details = e.details.get("errInfo", {}).get("details", {})
            reasons = []
            for rule in details.get("schemaRulesNotSatisfied", []):
                for prop in rule.get("propertiesNotSatisfied", []):
                    reasons.append(f"{prop['propertyName']}: {first_reason(prop['details'])}")
                if rule.get("missingProperties"):
                    reasons.append(f"missing {rule['missingProperties']}")
            show(f"REJECTED ({label})", f"code {e.code} '{e.details.get('errmsg')}'")
            for r in reasons:
                print(f"      - {r}")
    try:
        db.encounters.insert_one({"type": "Outpatient", "date": datetime.now(),
                                  "patient": {"patient_id": 1, "hospital_no": "SGH-0000001"}})
    except WriteError as e:
        show("REJECTED (encounter without a doctor)", f"code {e.code} '{e.details.get('errmsg')}'")


def f2_evolution() -> None:
    heading("F2", "Schema evolution: NHIA introduces an enrolee ID; store it for one patient, no ALTER TABLE")
    db.patients.update_one({"_id": 1}, {"$set": {"insurance.nhia_enrolee_id": "NHIA-LAG-0042117"}})
    show("patient 1 insurance now", db.patients.find_one({"_id": 1}, {"_id": 0, "insurance": 1}))
    show("patients that have the new field", db.patients.count_documents({"insurance.nhia_enrolee_id": {"$exists": True}}))
    show("other 49,999 documents untouched, e.g. patient 2",
         db.patients.find_one({"_id": 2}, {"_id": 0, "insurance": 1}) or "{} (uninsured, no field at all)")
    db.patients.update_one({"_id": 1}, {"$unset": {"insurance.nhia_enrolee_id": ""}})
    print("  (field removed again to restore the original data)")


def f3_polymorphism() -> None:
    heading("F3", "Polymorphism: lab results of different shapes live side by side in `encounters`")
    shapes = db.encounters.aggregate([
        {"$match": {"labs.result": {"$exists": True}}},
        {"$unwind": "$labs"},
        {"$match": {"labs.result": {"$exists": True}}},
        {"$project": {"test": "$labs.test_code",
                      "shape": {"$sortArray": {"input": {"$map": {"input": {"$objectToArray": "$labs.result"},
                                                                  "in": "$$this.k"}}, "sortBy": 1}}}},
        {"$group": {"_id": "$shape", "results": {"$sum": 1}, "tests": {"$addToSet": "$test"}}},
        {"$sort": {"results": -1}},
    ])
    for s in shapes:
        print(f"  {s['results']:>7,} results  fields={s['_id']}  tests={sorted(s['tests'])}")


def f4_single_document_atomicity() -> None:
    heading("F4", "Single-document atomicity: dispense a whole prescription in ONE update")
    src = db.encounters.find_one({"prescription.status": "Dispensed",
                                  "prescription.items.1": {"$exists": True}})
    demo = dict(src, _id=-1)
    demo["prescription"] = dict(src["prescription"], status="Pending")
    demo["prescription"]["items"] = [dict(i, dispensed=0) for i in src["prescription"]["items"]]
    demo["prescription"].pop("dispensed_at", None)
    demo["prescription"].pop("dispensed_by", None)
    db.encounters.insert_one(demo)
    show("before", db.encounters.find_one({"_id": -1}, {"_id": 0, "prescription": 1}))
    res = db.encounters.update_one(
        {"_id": -1, "prescription.status": "Pending"},                      # guard: only if still pending
        [{"$set": {"prescription.status": "Dispensed",
                   "prescription.dispensed_at": "$$NOW",
                   "prescription.items": {"$map": {"input": "$prescription.items",
                                                   "in": {"$mergeObjects": ["$$this", {"dispensed": "$$this.quantity"}]}}}}}])
    show("update result", f"matched={res.matched_count} modified={res.modified_count}")
    show("after", db.encounters.find_one({"_id": -1}, {"_id": 0, "prescription": 1}))
    print("  In PostgreSQL this touched 2 tables and needed a transaction (Portfolio 3, T1);")
    print("  here the prescription and all its items are one document, so one write is atomic.")
    res = db.encounters.update_one({"_id": -1, "prescription.status": "Pending"},
                                   {"$set": {"prescription.status": "Dispensed"}})
    show("second pharmacist tries the same prescription", f"matched={res.matched_count} (already dispensed - nothing done)")
    db.encounters.delete_one({"_id": -1})


def f5_concurrent_decrements() -> None:
    heading("F5", "No lost update without transactions: 20 pharmacists take 10 capsules each from stock 100")
    db.drugs.insert_one({"_id": -1, "generic_name": "DEMO Amoxicillin", "form": "Capsule", "strength": "500 mg",
                         "stock": 100})

    def take(_):
        r = db.drugs.update_one({"_id": -1, "stock": {"$gte": 10}}, {"$inc": {"stock": -10}})
        return r.modified_count

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(take, range(20)))
    final = db.drugs.find_one({"_id": -1})["stock"]
    show("successful dispenses", sum(outcomes))
    show("refused (not enough stock)", outcomes.count(0))
    show("final stock", final)
    print("  The filter {stock >= 10} and the $inc are evaluated atomically on the document,")
    print("  so stock never goes negative and no decrement is lost (compare Portfolio 3, C1a).")
    db.drugs.delete_one({"_id": -1})


def f6_transactions() -> None:
    heading("F6", "Multi-document transaction (pay an invoice AND update the patient's outstanding balance)")
    hello = db.command("hello")
    show("server topology", "replica set " + hello["setName"] if "setName" in hello else "standalone (no replica set)")
    try:
        with client.start_session() as s:
            with s.start_transaction():
                db.encounters.update_one({"_id": 1}, {"$set": {"billing.status": "Paid"}}, session=s)
                db.patients.update_one({"_id": 1}, {"$inc": {"stats.outstanding": 0}}, session=s)
        show("result", "committed")
    except OperationFailure as e:
        show("result", f"FAILED code {e.code}: {e.details.get('errmsg')}")
        print("  MongoDB needs a replica set (or sharded cluster) for multi-document transactions,")
        print("  because they rely on the replication oplog. Portfolio 5 builds a replica set and repeats this.")


def f7_ttl_cache() -> None:
    heading("F7", "Key-value cache with TTL: a patient summary that expires on its own")
    key = "patient:summary:demo"
    now = datetime.now(timezone.utc)
    db.cache.replace_one({"_id": key}, {"_id": key, "value": {"hospital_no": "SGH-0000001", "allergies": []},
                                        "expires_at": now + timedelta(seconds=5)}, upsert=True)
    show("SET", f"{key}  (expires in 5 s)")
    show("GET immediately", db.cache.find_one({"_id": key}, {"_id": 0, "value": 1}))
    ttl = db.cache.index_information()["ttl"]
    show("TTL index", f"key={ttl['key']} expireAfterSeconds={ttl['expireAfterSeconds']}")
    start = time.time()
    while db.cache.find_one({"_id": key}) and time.time() - start < 120:
        time.sleep(2)
    gone = db.cache.find_one({"_id": key}) is None
    show("GET after expiry", f"{'not found' if gone else 'STILL PRESENT'} "
                             f"(removed by the TTL monitor {time.time() - start:.0f} s after the check started;"
                             " it runs every 60 s, so expiry is approximate)")


def f8_queue() -> None:
    heading("F8", "Atomic work queue: two nurses call the next waiting patient at the same time")
    base = datetime.now()
    db.queue.insert_many([{"clinic": "DEMO-GOPD", "ticket": i, "hospital_no": f"SGH-{i:07d}",
                           "status": "waiting", "checked_in_at": base + timedelta(seconds=i)} for i in range(1, 13)])

    def nurse(name):
        called = []
        while True:
            doc = db.queue.find_one_and_update(
                {"clinic": "DEMO-GOPD", "status": "waiting"},
                {"$set": {"status": "called", "nurse": name, "called_at": datetime.now()}},
                sort=[("checked_in_at", 1)], return_document=ReturnDocument.AFTER)
            if doc is None:
                return called
            called.append(doc["ticket"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(nurse, ["Nurse A", "Nurse B"])
    show("Nurse A called tickets", a)
    show("Nurse B called tickets", b)
    counts = Counter(a + b)
    show("tickets called twice", [t for t, n in counts.items() if n > 1] or "none")
    show("tickets never called", sorted(set(range(1, 13)) - set(counts)) or "none")
    db.queue.delete_many({"clinic": "DEMO-GOPD"})


if __name__ == "__main__":
    print(f"MongoDB {client.server_info()['version']}  database '{MONGO_DB}'")
    for f in (f1_validation, f2_evolution, f3_polymorphism, f4_single_document_atomicity,
              f5_concurrent_decrements, f6_transactions, f7_ttl_cache, f8_queue):
        f()
    print("\nDone - demo documents removed.")
