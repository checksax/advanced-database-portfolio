"""
Portfolio 4: build the MongoDB model of St. Grace Specialist Hospital from
the PostgreSQL database of Portfolio 1.

Collections
  patients    document  - demographics, embedded allergies/insurance, computed stats,
                          subset of the 3 most recent visits
  encounters  document  - one visit = one document: doctor, vitals, diagnoses,
                          prescription, polymorphic lab results, billing summary
  drugs       document  - catalogue; drug-specific properties use the attribute pattern
  staff       document  - staff directory; doctor details as an embedded sub-document
  referrals   graph     - doctor -> doctor referral edges (traversed with $graphLookup)
  vitals      time series - 4-hourly inpatient observations, last 60 days
  cache       key-value - "patient:summary:<id>" -> summary, expired by a TTL index
  queue       key-value - clinic check-in queue served with atomic findOneAndUpdate

Money is stored as Decimal128 (exact), clinical measurements as doubles.
Some lab-result details (FBC panel, culture sensitivities, parasite species)
do not exist in the relational data; they are added here, deterministically,
to show schema flexibility, and are synthetic like the rest of the data.

Usage:  python migrate.py          (drops and rebuilds the MongoDB database)
"""

import math
import random
import time
from collections import defaultdict
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal

import psycopg
from bson.decimal128 import Decimal128
from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient

from common import AS_OF, MONGO_DB, MONGO_URI, PG_DSN

rng = random.Random(8103)
BATCH = 5000


def money(x) -> Decimal128:
    return Decimal128(Decimal(x if x is not None else 0).quantize(Decimal("0.01")))


def num(x):
    return float(x) if x is not None else None


def age_on(dob, when) -> int:
    d = when.date() if hasattr(when, "date") else when
    return d.year - dob.year - ((d.month, d.day) < (dob.month, dob.day))


def compact(d: dict) -> dict:
    """Drop None values: absent fields are simply not stored (sparse documents)."""
    return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}


# ---------------------------------------------------------------------------
# Schema validation ($jsonSchema). Flexible, but not schemaless.
# ---------------------------------------------------------------------------
PATIENT_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["hospital_no", "name", "date_of_birth", "gender", "contact", "registered_at"],
        "properties": {
            "hospital_no": {"bsonType": "string", "pattern": "^SGH-[0-9]{7}$"},
            "name": {"bsonType": "object", "required": ["first", "last"],
                     "properties": {"first": {"bsonType": "string", "minLength": 1},
                                    "last": {"bsonType": "string", "minLength": 1}}},
            "date_of_birth": {"bsonType": "date"},
            "gender": {"enum": ["M", "F"]},
            "blood_group": {"enum": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]},
            "genotype": {"enum": ["AA", "AS", "SS", "AC", "SC"]},
            "contact": {"bsonType": "object", "required": ["phone"],
                        "properties": {"phone": {"bsonType": "string", "pattern": "^0[789][01][0-9]{8}$"}}},
            "insurance": {"bsonType": "object", "required": ["provider", "policy_no", "coverage_percent"]},
            "allergies": {"bsonType": "array",
                          "items": {"bsonType": "object", "required": ["allergen", "severity"],
                                    "properties": {"severity": {"enum": ["Mild", "Moderate", "Severe"]}}}},
        },
    }
}
ENCOUNTER_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["type", "date", "patient", "doctor"],
        "properties": {
            "type": {"enum": ["Outpatient", "Inpatient", "Emergency"]},
            "date": {"bsonType": "date"},
            "patient": {"bsonType": "object", "required": ["patient_id", "hospital_no"]},
            "doctor": {"bsonType": "object", "required": ["staff_id", "name", "department"]},
            "vitals": {"bsonType": "object",
                       "properties": {"temperature_c": {"bsonType": "double", "minimum": 30, "maximum": 45},
                                      "pulse_bpm": {"bsonType": "int", "minimum": 20, "maximum": 250}}},
            "diagnoses": {"bsonType": "array", "items": {"bsonType": "object", "required": ["code", "type"]}},
            "billing": {"bsonType": "object",
                        "properties": {"status": {"enum": ["Unpaid", "Partial", "Paid", "Void"]}}},
        },
    }
}


def create_collections(db) -> None:
    db.create_collection("patients", validator=PATIENT_SCHEMA, validationLevel="strict", validationAction="error")
    db.create_collection("encounters", validator=ENCOUNTER_SCHEMA, validationLevel="strict", validationAction="error")
    for name in ("drugs", "staff", "referrals", "cache", "queue"):
        db.create_collection(name)
    db.create_collection("vitals", timeseries={"timeField": "ts", "metaField": "meta", "granularity": "hours"})


def create_indexes(db) -> None:
    db.patients.create_indexes([
        IndexModel("hospital_no", unique=True),
        # case-insensitive name search: collation strength 2 ignores case (Portfolio 2 needed lower() + an expression index)
        IndexModel([("name.last", ASCENDING), ("name.first", ASCENDING)],
                   name="name_ci", collation={"locale": "en", "strength": 2}),
    ])
    db.encounters.create_indexes([
        IndexModel([("patient.patient_id", ASCENDING), ("date", DESCENDING)], name="patient_history"),
        IndexModel([("diagnoses.code", ASCENDING), ("date", ASCENDING)], name="diagnosis_date"),   # multikey
        IndexModel([("date", ASCENDING)], name="date"),
        IndexModel([("labs.result.resulted_at", DESCENDING)], name="critical_labs",
                   partialFilterExpression={"labs.result.flag": "Critical"}),
    ])
    db.drugs.create_index([("attributes.k", ASCENDING), ("attributes.v", ASCENDING)], name="attributes")
    db.referrals.create_indexes([IndexModel("from_doctor"), IndexModel("to_doctor")])
    db.cache.create_index("expires_at", expireAfterSeconds=0, name="ttl")     # key-value expiry
    db.queue.create_index([("clinic", ASCENDING), ("status", ASCENDING), ("checked_in_at", ASCENDING)])


# ---------------------------------------------------------------------------
# Polymorphic lab results
# ---------------------------------------------------------------------------
ANTIBIOTICS = ["Ciprofloxacin", "Nitrofurantoin", "Ceftriaxone", "Amoxicillin-clavulanate", "Gentamicin"]


def lab_result(code, value, text, flag, unit, low, high, performed_by, resulted_at) -> dict:
    r = {"flag": flag, "performed_by": performed_by, "resulted_at": resulted_at}
    if value is not None:
        r.update(value=float(value), unit=unit, reference_range={"low": num(low), "high": num(high)})
        if code == "FBC":                     # a panel: several analytes in one result
            r["panel"] = {
                "haemoglobin_g_dl": float(value),
                "wbc_10e9_l": round(rng.uniform(4.0, 10.5) if flag == "Normal" else rng.uniform(11, 22), 1),
                "platelets_10e9_l": int(rng.uniform(150, 400) if flag == "Normal" else rng.uniform(60, 149)),
            }
        return r
    r["finding"] = text
    if code == "GENO":
        r["genotype"] = text
    elif code == "MPS" and flag != "Normal":
        r["species"] = "P. falciparum"
        r["density"] = text[text.find("(") + 1:text.find(")")]
    elif code == "MCS-U" and flag != "Normal":
        r["culture"] = {
            "organism": text.split(" >")[0],
            "colony_count": "> 10^5 CFU/ml",
            "sensitivities": [{"antibiotic": a, "result": rng.choice("SSSRI")} for a in rng.sample(ANTIBIOTICS, 4)],
        }
    return r


DRUG_ATTRIBUTES = {
    "Insulin (Soluble)": [("storage", "2-8 C cold chain"), ("high_alert", True)],
    "Ceftriaxone": [("reconstitution", "10 ml water for injection"), ("route", "IV/IM")],
    "Artesunate": [("route", "IV/IM"), ("who_essential", True)],
    "Artemether/Lumefantrine": [("take_with", "fatty food"), ("who_essential", True)],
    "Paracetamol": [("who_essential", True)],
    "Salbutamol": [("device", "metered-dose inhaler")],
    "Hydroxyurea": [("monitoring", "FBC every 4 weeks"), ("high_alert", True)],
    "Normal Saline": [("volume_ml", 500), ("route", "IV")],
    "Metformin": [("max_daily_dose_mg", 2550)],
    "Amlodipine": [("max_daily_dose_mg", 10)],
}


def main() -> None:
    t0 = time.time()
    client = MongoClient(MONGO_URI)
    client.drop_database(MONGO_DB)
    db = client[MONGO_DB]
    create_collections(db)

    with psycopg.connect(PG_DSN) as pg:
        cur = pg.cursor()
        cur.execute("SET search_path TO hospital, public")
        print("Reading PostgreSQL ...")

        depts = dict(cur.execute("SELECT department_id, name FROM departments"))
        staff_rows = cur.execute("""SELECT s.staff_id, s.staff_no, s.first_name, s.last_name, s.role, s.department_id,
                                           s.email, s.phone, s.hire_date, s.is_active, d.specialty, d.license_no,
                                           d.consultation_fee
                                    FROM staff s LEFT JOIN doctors d USING (staff_id) ORDER BY s.staff_id""").fetchall()
        doctor_ref = {}
        staff_docs = []
        for (sid, sno, fn, ln, role, did, email, phone, hire, active, spec, lic, fee) in staff_rows:
            doc = compact({"_id": sid, "staff_no": sno, "name": {"first": fn, "last": ln}, "role": role,
                           "department": depts[did], "email": email, "phone": phone,
                           "hire_date": datetime.combine(hire, dtime()) if hire else None,
                           "active": active})
            if role == "Doctor":
                doc["doctor"] = {"specialty": spec, "license_no": lic, "consultation_fee": money(fee)}
                doctor_ref[sid] = {"staff_id": sid, "name": f"Dr. {fn} {ln}", "specialty": spec,
                                   "department": depts[did]}
            staff_docs.append(doc)

        icd = {c: (d, cat) for c, d, cat in cur.execute("SELECT code, description, category FROM icd10_codes")}
        tests = {r[0]: r[1:] for r in cur.execute(
            "SELECT test_id, test_code, name, unit, normal_low, normal_high FROM lab_tests")}
        drugs = cur.execute("""SELECT drug_id, generic_name, brand_name, dosage_form, strength, unit_price,
                                      stock_quantity, reorder_level FROM drugs ORDER BY drug_id""").fetchall()
        drug_info = {d[0]: d for d in drugs}

        patients = cur.execute("""SELECT p.patient_id, hospital_no, first_name, last_name, date_of_birth, gender,
                                         blood_group, genotype, p.phone, email, address, state_of_origin,
                                         next_of_kin_name, next_of_kin_phone, ip.name, ip.coverage_percent,
                                         policy_no, registered_at
                                  FROM patients p LEFT JOIN insurance_providers ip USING (provider_id)
                                  ORDER BY p.patient_id""").fetchall()
        dob = {p[0]: p[4] for p in patients}
        allergies = defaultdict(list)
        for pid, allergen, sev in cur.execute("SELECT patient_id, allergen, severity FROM patient_allergies"):
            allergies[pid].append({"allergen": allergen, "severity": sev})

        diagnoses = defaultdict(list)
        for eid, code, dtype in cur.execute(
                "SELECT encounter_id, icd10_code, diagnosis_type FROM diagnoses ORDER BY encounter_id, diagnosis_type"):
            desc, cat = icd[code]
            diagnoses[eid].append({"code": code, "description": desc, "category": cat, "type": dtype})

        rx_items = defaultdict(list)
        for rid, did, dose, freq, days, qty, disp in cur.execute(
                "SELECT prescription_id, drug_id, dose, frequency, duration_days, quantity, dispensed_quantity "
                "FROM prescription_items"):
            _, gname, _, form, strength, *_ = drug_info[did]
            rx_items[rid].append({"drug_id": did, "drug": gname, "form": form, "strength": strength, "dose": dose,
                                  "frequency": freq, "days": days, "quantity": qty, "dispensed": disp})
        prescriptions = {}
        for rid, eid, status, by, at in cur.execute(
                "SELECT prescription_id, encounter_id, status, dispensed_by, dispensed_at FROM prescriptions"):
            prescriptions[eid] = compact({"prescription_id": rid, "status": status, "dispensed_by": by,
                                          "dispensed_at": at, "items": rx_items[rid]})

        labs = defaultdict(list)
        for (lo, eid, tid, prio, ordered, status, value, text, flag, by, at) in cur.execute(
                """SELECT lo.lab_order_id, lo.encounter_id, lo.test_id, lo.priority, lo.ordered_at, lo.status,
                          r.result_value, r.result_text, r.flag, r.performed_by, r.resulted_at
                   FROM lab_orders lo LEFT JOIN lab_results r USING (lab_order_id) ORDER BY lo.lab_order_id"""):
            code, name, unit, low, high = tests[tid]
            entry = {"order_id": lo, "test_code": code, "test": name, "priority": prio,
                     "ordered_at": ordered, "status": status}
            if flag is not None:
                entry["result"] = lab_result(code, value, text, flag, unit, low, high, by, at)
            labs[eid].append(entry)

        billing = {}
        for (eid, inv_no, status, total, paid) in cur.execute(
                """SELECT i.encounter_id, i.invoice_no, i.status, b.total_amount, b.amount_paid
                   FROM invoices i JOIN v_invoice_balance b USING (invoice_id)"""):
            billing[eid] = {"invoice_no": inv_no, "total": money(total), "paid": money(paid),
                            "balance": money(total - paid), "status": status}

        admissions = {}
        for (aid, bed_no, ward, ward_type, admitted, discharged, reason) in cur.execute(
                """SELECT a.admission_id, b.bed_number, w.name, w.ward_type, a.admitted_at, a.discharged_at,
                          a.admission_reason
                   FROM admissions a JOIN beds b USING (bed_id) JOIN wards w USING (ward_id)"""):
            admissions[aid] = compact({"admission_id": aid, "ward": ward, "ward_type": ward_type, "bed": bed_no,
                                       "admitted_at": admitted, "discharged_at": discharged, "reason": reason})

        encounters = cur.execute("""SELECT encounter_id, patient_id, doctor_id, appointment_id, admission_id,
                                           encounter_type, encounter_date, chief_complaint, temperature_c,
                                           bp_systolic, bp_diastolic, pulse_bpm, weight_kg
                                    FROM encounters ORDER BY encounter_id""").fetchall()

    # -------------------------------------------------------------- encounters
    print("Building documents ...")
    pinfo = {p[0]: (p[1], f"{p[2]} {p[3]}", p[5]) for p in patients}
    by_patient = defaultdict(list)          # for patient stats / subset / referrals
    enc_docs = []
    for (eid, pid, doc_id, appt, adm, etype, when, complaint, temp, sys_, dia, pulse, weight) in encounters:
        hno, name, gender = pinfo[pid]
        doc = compact({
            "_id": eid,
            "type": etype,
            "date": when,
            "chief_complaint": complaint,
            "patient": {"patient_id": pid, "hospital_no": hno, "name": name, "gender": gender,
                        "age_at_visit": age_on(dob[pid], when)},
            "doctor": doctor_ref[doc_id],
            "appointment_id": appt,
            "admission": admissions.get(adm),
            "vitals": compact({"temperature_c": num(temp),
                               "blood_pressure": {"systolic": sys_, "diastolic": dia} if sys_ else None,
                               "pulse_bpm": pulse, "weight_kg": num(weight)}),
            "diagnoses": diagnoses.get(eid),
            "prescription": prescriptions.get(eid),
            "labs": labs.get(eid),
            "billing": billing.get(eid),
        })
        enc_docs.append(doc)
        primary = diagnoses[eid][0]["code"] if diagnoses.get(eid) else None
        by_patient[pid].append((when, eid, etype, primary, doc_id, doc.get("billing")))

    # -------------------------------------------------------------- patients
    patient_docs = []
    for (pid, hno, fn, ln, d_o_b, gender, bg, geno, phone, email, addr, state, nok, nok_phone,
         insurer, cov, policy, registered) in patients:
        visits = sorted(by_patient.get(pid, []), reverse=True)
        billed = sum((v[5]["total"].to_decimal() for v in visits if v[5]), Decimal(0))
        outstanding = sum((v[5]["balance"].to_decimal() for v in visits if v[5]), Decimal(0))
        patient_docs.append(compact({
            "_id": pid,
            "hospital_no": hno,
            "name": {"first": fn, "last": ln},
            "date_of_birth": datetime.combine(d_o_b, dtime()),
            "gender": gender,
            "blood_group": bg,
            "genotype": geno,
            "contact": compact({"phone": phone, "email": email, "address": addr, "state_of_origin": state}),
            "next_of_kin": compact({"name": nok, "phone": nok_phone}),
            "insurance": {"provider": insurer, "coverage_percent": float(cov), "policy_no": policy} if insurer else None,
            "allergies": allergies.get(pid),
            "registered_at": registered,
            "stats": {"encounters": len(visits),
                      "admissions": sum(1 for v in visits if v[2] == "Inpatient"),
                      "last_visit": visits[0][0] if visits else None,
                      "total_billed": money(billed), "outstanding": money(outstanding)},
            "recent_encounters": [compact({"encounter_id": v[1], "date": v[0], "type": v[2],
                                           "primary_diagnosis": v[3]}) for v in visits[:3]],
        }))

    # -------------------------------------------------------------- drugs
    drug_docs = [compact({"_id": d[0], "generic_name": d[1], "brand_name": d[2], "form": d[3], "strength": d[4],
                          "unit_price": money(d[5]), "stock": d[6], "reorder_level": d[7],
                          "attributes": [{"k": k, "v": v} for k, v in DRUG_ATTRIBUTES.get(d[1], [])]})
                 for d in drugs]

    # -------------------------------------------------------------- referrals (graph edges)
    # A referral is inferred when a patient's next outpatient visit, within 30 days,
    # is with a doctor in a different department.
    referral_docs = []
    for pid, visits in by_patient.items():
        outp = sorted(v for v in visits if v[2] == "Outpatient")
        for (d1, e1, _, dx1, doc1, _), (d2, e2, _, _, doc2, _) in zip(outp, outp[1:]):
            if d2 - d1 <= timedelta(days=30) and doctor_ref[doc1]["department"] != doctor_ref[doc2]["department"]:
                referral_docs.append({"from_doctor": doc1, "to_doctor": doc2,
                                      "from_department": doctor_ref[doc1]["department"],
                                      "to_department": doctor_ref[doc2]["department"],
                                      "patient_id": pid, "referred_at": d1, "reason": dx1,
                                      "from_encounter": e1, "to_encounter": e2})

    # -------------------------------------------------------------- vitals (time series)
    fever_codes = {"B54", "B50.9", "A01.0", "J18.9", "R50.9", "N39.0"}
    adm_dx = {}
    for doc in enc_docs:
        if doc["type"] == "Inpatient":
            adm_dx[doc["admission"]["admission_id"]] = (doc["patient"]["patient_id"],
                                                        doc.get("diagnoses", [{}])[0].get("code"))
    vital_docs = []
    window_start = AS_OF - timedelta(days=60)
    for aid, a in admissions.items():
        if a["admitted_at"] < window_start:
            continue
        pid, code = adm_dx[aid]
        fever = code in fever_codes
        end = min(a.get("discharged_at") or AS_OF, AS_OF)
        t = a["admitted_at"]
        while t <= end:
            hours = (t - a["admitted_at"]).total_seconds() / 3600
            temp = 36.9 + (2.2 * math.exp(-hours / 36) if fever else 0) + rng.gauss(0, 0.25)
            vital_docs.append({
                "ts": t,
                "meta": {"admission_id": aid, "patient_id": pid, "ward": a["ward"]},
                "temperature_c": round(temp, 1),
                "pulse_bpm": int(72 + (temp - 37) * 10 + rng.gauss(0, 6)),
                "spo2_pct": int(min(100, rng.gauss(96 if code != "J18.9" else 92, 1.5))),
                "bp_systolic": int(rng.gauss(150 if code in ("I10", "O14.9") else 122, 10)),
            })
            t += timedelta(hours=4)

    # -------------------------------------------------------------- load
    print("Loading MongoDB ...")
    for coll, docs in (("staff", staff_docs), ("drugs", drug_docs), ("patients", patient_docs),
                       ("encounters", enc_docs), ("referrals", referral_docs), ("vitals", vital_docs)):
        start = time.time()
        for i in range(0, len(docs), BATCH):
            db[coll].insert_many(docs[i:i + BATCH], ordered=False)
        print(f"  {coll:<11} {len(docs):>9,} documents  {time.time() - start:6.1f}s")

    print("Creating indexes ...")
    create_indexes(db)
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
