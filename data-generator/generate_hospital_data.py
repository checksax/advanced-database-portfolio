"""
St. Grace Specialist Hospital - synthetic data generator.

Rebuilds the `hospital` schema (01_schema.sql + 02_reference_data.sql) and
bulk-loads realistic, internally consistent transactional data with COPY:

    staff / doctors           ~380
    patients                  50,000  (+ allergies)
    appointments              ~200,000 (Nov 2024 - Dec 2026, no double-booking)
    admissions                ~9,000   (bed timelines never overlap)
    encounters                ~170,000 (outpatient, emergency, inpatient)
    diagnoses, prescriptions, lab orders/results, invoices, payments

The data is fictitious. A fixed random seed makes every run identical,
so query timings in Portfolio 2 are reproducible.

Usage:
    pip install -r requirements.txt
    python generate_hospital_data.py                # default DSN below
    set HOSPITAL_DSN=postgresql://user:pass@host:5432/db  (to override)
"""

import os
import random
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg

DSN = os.environ.get(
    "HOSPITAL_DSN",
    "postgresql://hospital_admin:hospital_pass@localhost:5432/stgrace_hospital",
)
SQL_DIR = Path(__file__).resolve().parent.parent / "portfolio-1-database-design" / "sql"

SEED = 8103
AS_OF = datetime(2026, 11, 3, 9, 0)          # "today" for the simulated hospital
START = datetime(2024, 11, 4)                # first day of recorded activity
FUTURE_END = datetime(2026, 12, 31)          # appointments are booked ahead to here
N_PATIENTS = 50_000
SLOT_FILL_RATE = 0.35                        # share of doctor slots that get booked

rng = random.Random(SEED)

# ---------------------------------------------------------------------------
# Name pools (Nigerian names across major ethnic groups)
# ---------------------------------------------------------------------------
MALE_FIRST = """Chinedu Emeka Obinna Ikechukwu Chukwuemeka Tunde Babatunde Olumide Segun Kunle
Ayodele Femi Adebayo Musa Ibrahim Abubakar Usman Aliyu Sani Yusuf Daniel David Samuel John
Joseph Michael Peter Paul Emmanuel Victor Godwin Ifeanyi Uche Nnamdi Kelechi Tobi Dapo
Gbenga Kehinde Taiwo Efe Osagie Ese Bassey Effiong Edet Terkula Aondona Danjuma Garba""".split()
FEMALE_FIRST = """Ngozi Chiamaka Adaeze Nneka Ifeoma Chioma Amaka Funmilayo Yetunde Bukola Folake
Kemi Titilayo Aisha Fatima Hauwa Zainab Amina Hadiza Maryam Grace Blessing Mercy Joy Faith
Esther Ruth Deborah Mary Elizabeth Patience Comfort Precious Favour Ebere Uju Tolu Damilola
Omolara Abimbola Osaro Ivie Eno Ekaete Mfon Doosuur Ladi Asabe Rahila""".split()
LAST_NAMES = """Okafor Okonkwo Nwosu Eze Obi Chukwu Nwankwo Okeke Onyeka Ibe Adeyemi Ogunleye
Adebayo Oladipo Afolabi Balogun Ogunbiyi Adewale Olatunji Akinola Bello Abdullahi Mohammed
Suleiman Danladi Yakubu Lawal Aminu Idris Garba Etim Bassey Okon Effiong Udoh Ekpo Edo
Osagie Igbinedion Omoregie Ighodaro Tor Agbo Ochei Ojo Oyelaran Uzor Nnadi Anyanwu Mbah""".split()
STATES = """Lagos Oyo Ogun Osun Ondo Ekiti Kwara Kano Kaduna Katsina Sokoto Niger Plateau Benue
Enugu Anambra Imo Abia Ebonyi Rivers Delta Edo Bayelsa Cross-River Akwa-Ibom FCT Kogi""".split()
STREETS = """Allen Avenue|Adeola Odeku Street|Awolowo Road|Herbert Macaulay Way|Ikorodu Road|
Ahmadu Bello Way|Aminu Kano Crescent|Ogui Road|Aba Road|Murtala Mohammed Way|Ring Road|
Bode Thomas Street|Opebi Road|Admiralty Way|Agege Motor Road""".replace("\n", "").split("|")

ALLERGENS = ["Penicillin", "Sulfonamides", "Aspirin", "Ibuprofen", "Chloroquine",
             "Peanuts", "Shellfish", "Latex", "Eggs", "Dust mites"]

# ---------------------------------------------------------------------------
# Clinical knowledge used to keep the data coherent
# ---------------------------------------------------------------------------
DEPT_DOCTORS = {               # department -> (number of doctors, specialties)
    "General Medicine": (20, ["Internal Medicine", "Family Medicine"]),
    "Paediatrics": (12, ["Paediatrics"]),
    "Obstetrics & Gynaecology": (12, ["Obstetrics & Gynaecology"]),
    "Surgery": (12, ["General Surgery", "Orthopaedic Surgery"]),
    "Cardiology": (8, ["Cardiology"]),
    "Emergency": (12, ["Emergency Medicine"]),
    "Radiology": (4, ["Radiology"]),
}
OTHER_STAFF = [               # (role, department, count)
    ("Nurse", "General Medicine", 60), ("Nurse", "Paediatrics", 30),
    ("Nurse", "Obstetrics & Gynaecology", 35), ("Nurse", "Surgery", 30),
    ("Nurse", "Cardiology", 15), ("Nurse", "Emergency", 30),
    ("LabScientist", "Laboratory", 25), ("Pharmacist", "Pharmacy", 15),
    ("Cashier", "Administration & Finance", 15),
    ("RecordsOfficer", "Administration & Finance", 15),
    ("Administrator", "Administration & Finance", 10),
]
CLINIC_DEPTS = ["General Medicine", "Paediatrics", "Obstetrics & Gynaecology",
                "Surgery", "Cardiology"]          # departments that run appointment clinics

DEPT_DIAGNOSES = {
    "General Medicine": ["B54", "B50.9", "A01.0", "I10", "E11.9", "J06.9", "K29.7", "N39.0",
                         "R50.9", "Z00.0", "D64.9", "M54.5", "E66.9", "A09", "B20", "A15.0"],
    "Paediatrics": ["B54", "B50.9", "A09", "J06.9", "J18.9", "D57.1", "J45.9", "R50.9", "D64.9"],
    "Obstetrics & Gynaecology": ["O80", "O14.9", "N39.0", "D64.9", "Z00.0"],
    "Surgery": ["K35.8", "S52.5", "M54.5"],
    "Cardiology": ["I10", "I50.9", "I63.9", "E11.9"],
    "Emergency": ["B50.9", "J18.9", "S52.5", "I63.9", "K35.8", "A09", "J45.9", "O14.9", "D57.1"],
}
DIAGNOSIS_DRUGS = {
    "B54": ["Artemether/Lumefantrine", "Paracetamol"], "B50.9": ["Artesunate", "Paracetamol"],
    "A01.0": ["Ciprofloxacin", "Paracetamol"], "A09": ["Oral Rehydration Salts", "Metronidazole"],
    "B20": ["Folic Acid"], "A15.0": ["Ferrous Sulphate"], "I10": ["Amlodipine", "Lisinopril"],
    "I50.9": ["Lisinopril"], "I63.9": ["Normal Saline", "Amlodipine"],
    "E11.9": ["Metformin", "Insulin (Soluble)"], "E66.9": ["Metformin"],
    "D57.1": ["Hydroxyurea", "Folic Acid"], "D64.9": ["Ferrous Sulphate", "Folic Acid"],
    "J18.9": ["Ceftriaxone", "Amoxicillin"], "J45.9": ["Salbutamol", "Hydrocortisone"],
    "J06.9": ["Amoxicillin", "Paracetamol"], "K29.7": ["Omeprazole"],
    "K35.8": ["Ceftriaxone", "Metronidazole", "Normal Saline"], "N39.0": ["Ciprofloxacin"],
    "O80": ["Ferrous Sulphate", "Paracetamol"], "O14.9": ["Normal Saline"],
    "M54.5": ["Ibuprofen"], "S52.5": ["Ibuprofen", "Paracetamol"], "R50.9": ["Paracetamol"],
    "Z00.0": [],
}
DIAGNOSIS_TESTS = {
    "B54": ["MPS", "FBC"], "B50.9": ["MPS", "PCV"], "A01.0": ["WIDAL", "FBC"],
    "A09": ["STOOL", "EUCR"], "B20": ["HIV", "FBC"], "A15.0": ["AFB"],
    "I10": ["EUCR", "LIPID"], "I50.9": ["EUCR"], "I63.9": ["LIPID", "FBS"],
    "E11.9": ["FBS", "HBA1C"], "E66.9": ["LIPID", "FBS"], "D57.1": ["GENO", "PCV"],
    "D64.9": ["FBC", "PCV"], "J18.9": ["FBC"], "J45.9": [], "J06.9": [], "K29.7": ["LFT"],
    "K35.8": ["FBC"], "N39.0": ["UA", "MCS-U"], "O80": ["PCV"], "O14.9": ["UA", "EUCR"],
    "M54.5": [], "S52.5": [], "R50.9": ["MPS", "FBC"], "Z00.0": ["FBC", "FBS", "UA"],
}
QUALITATIVE = {
    "MPS": (["Not seen"], ["Seen (+)", "Seen (++)", "Seen (+++)"]),
    "WIDAL": (["Non-reactive"], ["Reactive 1:160", "Reactive 1:320"]),
    "HIV": (["Non-reactive"], ["Reactive"]),
    "UA": (["NAD"], ["Protein ++", "Glucose +", "Leucocytes +++"]),
    "MCS-U": (["No growth"], ["E. coli >10^5 CFU/ml", "Klebsiella spp. >10^5 CFU/ml"]),
    "STOOL": (["No ova or cyst seen"], ["Cysts of E. histolytica seen", "Ova of A. lumbricoides seen"]),
    "AFB": (["No AFB seen"], ["AFB seen (scanty)", "AFB seen (2+)"]),
}
DOSING = {"Tablet": ("1 tab", "Twice daily"), "Capsule": ("1 cap", "Three times daily"),
          "Syrup": ("10 ml", "Three times daily"), "Injection": ("1 vial", "Once daily"),
          "Infusion": ("500 ml", "Every 8 hours"), "Cream": ("Apply thinly", "Twice daily"),
          "Inhaler": ("2 puffs", "As needed"), "Suspension": ("1 sachet", "After each stool")}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def phone() -> str:
    return rng.choice(["0803", "0805", "0806", "0703", "0813", "0816", "0902", "0809"]) + \
        f"{rng.randrange(10**7):07d}"


def skewed_pick(pool: list):
    """Pick from pool with a long-tail skew: a minority of patients visit very often."""
    return pool[int(len(pool) * rng.random() ** 1.8)]


def age_on(dob: date, when: datetime) -> int:
    d = when.date()
    return d.year - dob.year - ((d.month, d.day) < (dob.month, dob.day))


def run_sql_file(cur, path: Path) -> None:
    print(f"  running {path.name}")
    cur.execute(path.read_text(encoding="utf-8"))


def copy_rows(cur, table: str, cols: list[str], rows) -> int:
    n = 0
    with cur.copy(f"COPY hospital.{table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
            n += 1
    print(f"  {table:<20} {n:>9,} rows")
    return n


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def main() -> None:
    t0 = time.time()
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        print("Rebuilding schema ...")
        run_sql_file(cur, SQL_DIR / "01_schema.sql")
        run_sql_file(cur, SQL_DIR / "02_reference_data.sql")

        dept_id = dict(cur.execute("SELECT name, department_id FROM hospital.departments"))
        dept_name = {v: k for k, v in dept_id.items()}
        provider_cov = dict(cur.execute("SELECT provider_id, coverage_percent FROM hospital.insurance_providers"))
        drugs = {r[0]: r[1:] for r in cur.execute(
            "SELECT generic_name, drug_id, dosage_form, unit_price FROM hospital.drugs")}
        tests = {r[0]: r[1:] for r in cur.execute(
            "SELECT test_code, test_id, name, normal_low, normal_high, price FROM hospital.lab_tests")}
        beds = cur.execute("""SELECT b.bed_id, w.ward_type, w.daily_rate, w.name
                              FROM hospital.beds b JOIN hospital.wards w USING (ward_id)
                              ORDER BY b.bed_id""").fetchall()

        print("Generating data ...")
        # ---------------- staff & doctors ----------------
        staff_rows, doctor_rows = [], []
        doctors_by_dept: dict[str, list[int]] = {}
        staff_by_role: dict[str, list[int]] = {}
        doctor_fee: dict[int, Decimal] = {}
        used_emails = set()

        def add_staff(role: str, dept: str) -> int:
            sid = len(staff_rows) + 1
            male = rng.random() < 0.5
            fn = rng.choice(MALE_FIRST if male else FEMALE_FIRST)
            ln = rng.choice(LAST_NAMES)
            email = f"{fn}.{ln}".lower()
            while email in used_emails:
                email = f"{fn}.{ln}{rng.randrange(1000)}".lower()
            used_emails.add(email)
            hire = date(2005, 1, 1) + timedelta(days=rng.randrange(7300))
            staff_rows.append((f"STF-{sid:05d}", fn, ln, role, dept_id[dept],
                               f"{email}@stgrace.example", phone(), hire, rng.random() > 0.02))
            staff_by_role.setdefault(role, []).append(sid)
            return sid

        for dept, (count, specialties) in DEPT_DOCTORS.items():
            for _ in range(count):
                sid = add_staff("Doctor", dept)
                fee = Decimal(rng.choice([5000, 7500, 10000, 15000, 20000]))
                doctor_rows.append((sid, rng.choice(specialties), f"MDCN/{rng.randrange(10**5):05d}/{sid}", fee))
                doctors_by_dept.setdefault(dept, []).append(sid)
                doctor_fee[sid] = fee
        for role, dept, count in OTHER_STAFF:
            for _ in range(count):
                add_staff(role, dept)
        all_doctors = [d for ds in doctors_by_dept.values() for d in ds]

        # ---------------- patients ----------------
        patient_rows, allergy_rows = [], []
        patient_dob, patient_gender, patient_cov, patient_geno = {}, {}, {}, {}
        children, adults, women_of_age = [], [], []
        for pid in range(1, N_PATIENTS + 1):
            male = rng.random() < 0.48
            age_days = int(rng.triangular(0, 90 * 365, 28 * 365))
            dob = AS_OF.date() - timedelta(days=age_days)
            fn = rng.choice(MALE_FIRST if male else FEMALE_FIRST)
            ln = rng.choice(LAST_NAMES)
            geno = rng.choices(["AA", "AS", "SS", "AC", "SC"], [74, 22, 2, 1.5, 0.5])[0]
            bg = rng.choices(["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"], [51, 22, 20, 3, 2, 1, 0.7, 0.3])[0]
            insured = rng.random() < 0.35
            prov = rng.choice(list(provider_cov)) if insured else None
            reg = START - timedelta(days=rng.randrange(3650)) if rng.random() < 0.6 \
                else START + timedelta(days=rng.randrange((AS_OF - START).days))
            reg = max(reg, datetime.combine(dob, datetime.min.time()))
            patient_rows.append((
                f"SGH-{pid:07d}", fn, ln, dob, "M" if male else "F", bg, geno, phone(),
                f"{fn}.{ln}{pid}@mail.example".lower() if rng.random() < 0.6 else None,
                f"{rng.randrange(1, 250)} {rng.choice(STREETS)}", rng.choice(STATES),
                f"{rng.choice(MALE_FIRST + FEMALE_FIRST)} {ln}", phone(),
                prov, f"POL-{rng.randrange(10**8):08d}" if insured else None, reg,
            ))
            patient_dob[pid], patient_gender[pid], patient_geno[pid] = dob, ("M" if male else "F"), geno
            patient_cov[pid] = provider_cov[prov] if prov else Decimal(0)
            a = age_on(dob, AS_OF)
            (children if a < 16 else adults).append(pid)
            if not male and 16 <= a <= 48:
                women_of_age.append(pid)
            if rng.random() < 0.10:
                for allergen in rng.sample(ALLERGENS, rng.choice([1, 1, 2])):
                    allergy_rows.append((pid, allergen, rng.choice(["Mild", "Moderate", "Severe"])))

        def patient_for(dept: str) -> int:
            if dept == "Paediatrics":
                return skewed_pick(children)
            if dept == "Obstetrics & Gynaecology":
                return skewed_pick(women_of_age)
            return skewed_pick(adults)

        # ---------------- appointments (slot based => never overlap) ----------------
        appts = []          # (patient, doctor, start, end, status, reason, booked_at, dept)
        day = START
        while day <= FUTURE_END:
            if day.weekday() < 5:
                for dept in CLINIC_DEPTS:
                    for doc in doctors_by_dept[dept]:
                        for slot in range(16):                  # 08:00 - 16:00, 30-minute slots
                            if rng.random() >= SLOT_FILL_RATE:
                                continue
                            s = day + timedelta(hours=8, minutes=30 * slot)
                            booked = s - timedelta(days=rng.randrange(1, 30), hours=rng.randrange(8))
                            if s > AS_OF:
                                status = "Scheduled"
                            else:
                                status = rng.choices(["Completed", "NoShow", "Cancelled"], [80, 8, 12])[0]
                            appts.append((patient_for(dept), doc, s, s + timedelta(minutes=30),
                                          status, None, booked, dept))
            day += timedelta(days=1)
        appts.sort(key=lambda a: a[2])

        # ---------------- admissions (per-bed timeline => never overlap) ----------------
        adm = []            # (patient, bed_id, doctor, admitted, discharged, reason, ward_rate, dept)
        active_patients = set()
        ward_dept = {"General": "General Medicine", "Private": "General Medicine", "ICU": "Emergency",
                     "Paediatric": "Paediatrics", "Maternity": "Obstetrics & Gynaecology", "Surgical": "Surgery"}
        bed_occupied = []
        for bed_id, ward_type, rate, ward_name in beds:
            dept = ward_dept[ward_type]
            t = START + timedelta(hours=rng.randrange(72))
            while True:
                t += timedelta(hours=rng.randrange(4, 120))
                if t >= AS_OF:
                    break
                stay = timedelta(days=rng.choice([1, 2, 2, 3, 3, 4, 5, 7, 10, 14]), hours=rng.randrange(24))
                discharged = t + stay
                pid = patient_for(dept)
                if discharged >= AS_OF:                       # still on the ward today
                    if rng.random() < 0.25 or pid in active_patients:
                        break                                 # bed left free
                    active_patients.add(pid)
                    discharged = None
                    bed_occupied.append(bed_id)
                reason = {"Maternity": "Labour / antenatal care", "ICU": "Critical care",
                          "Surgical": "Surgical management", "Paediatric": "Acute illness in child"}.get(
                    ward_type, "Medical management")
                adm.append((pid, bed_id, rng.choice(doctors_by_dept[dept]), t, discharged, reason, rate, dept))
                if discharged is None:
                    break
                t = discharged
        adm.sort(key=lambda a: a[3])

        # ---------------- encounters ----------------
        enc = []            # (patient, doctor, appt_id, adm_id, type, when, complaint, dept, vitals)
        for i, a in enumerate(appts, start=1):
            if a[4] == "Completed":
                enc.append((a[0], a[1], i, None, "Outpatient", a[2] + timedelta(minutes=rng.randrange(25)), a[7]))
        n_emergency = int((AS_OF - START).days * 20)          # ~20 emergency visits a day
        for _ in range(n_emergency):
            when = START + timedelta(seconds=rng.randrange(int((AS_OF - START).total_seconds())))
            pid = skewed_pick(adults if rng.random() < 0.8 else children)
            enc.append((pid, rng.choice(doctors_by_dept["Emergency"]), None, None, "Emergency", when, "Emergency"))
        for i, a in enumerate(adm, start=1):
            enc.append((a[0], a[2], None, i, "Inpatient", a[3] + timedelta(minutes=rng.randrange(60)), a[7]))
        enc.sort(key=lambda e: e[5])

        complaints = {"B54": "Fever and body aches", "B50.9": "High fever, vomiting", "A01.0": "Prolonged fever",
                      "A09": "Diarrhoea and vomiting", "I10": "Headache, BP review", "E11.9": "Polyuria, diabetes review",
                      "J06.9": "Cough and catarrh", "J18.9": "Cough, difficulty breathing", "K29.7": "Epigastric pain",
                      "N39.0": "Painful urination", "O80": "Labour pains", "O14.9": "Swollen legs, high BP in pregnancy",
                      "K35.8": "Right lower abdominal pain", "S52.5": "Fall on outstretched hand",
                      "I50.9": "Breathlessness, leg swelling", "I63.9": "Sudden weakness on one side",
                      "D57.1": "Bone pains", "D64.9": "Weakness, dizziness", "J45.9": "Wheezing",
                      "M54.5": "Lower back pain", "Z00.0": "Routine medical check-up", "R50.9": "Fever",
                      "E66.9": "Weight management", "B20": "HIV clinic follow-up", "A15.0": "Chronic cough, night sweats"}

        enc_rows, dx_rows, rx_rows, rx_item_rows, lab_order_rows, lab_result_rows = [], [], [], [], [], []
        enc_charges: dict[int, list] = {}     # encounter id -> invoice lines
        pharmacists, lab_scis = staff_by_role["Pharmacist"], staff_by_role["LabScientist"]

        for eid, (pid, doc, appt_id, adm_id, etype, when, dept) in enumerate(enc, start=1):
            age = age_on(patient_dob[pid], when)
            codes = DEPT_DIAGNOSES[dept]
            primary = rng.choice(codes)
            if primary.startswith("O") and (patient_gender[pid] == "M" or not 15 <= age <= 50):
                primary = "B54"
            if primary == "D57.1" and patient_geno[pid] not in ("SS", "SC"):
                primary = "B54"
            dx = [primary]
            if rng.random() < 0.25:
                second = rng.choice(codes)
                if second not in dx and not second.startswith("O") and second != "D57.1":
                    dx.append(second)
            vit = (Decimal(f"{rng.gauss(37.6 if primary in ('B54', 'B50.9', 'A01.0', 'R50.9', 'J18.9') else 36.8, 0.5):.1f}"),
                   int(rng.gauss(150 if primary in ("I10", "O14.9") else 122, 14)), int(rng.gauss(82, 9)),
                   int(rng.gauss(84, 12)),
                   Decimal(f"{max(2.5, rng.gauss(14 + age * 2.5, 4) if age < 16 else rng.gauss(72, 14)):.1f}"))
            enc_rows.append((pid, doc, appt_id, adm_id, etype, when, complaints.get(primary, "General complaint"),
                             None, *vit))
            charges = [("Consultation", "Doctor consultation", 1, doctor_fee[doc])]
            for k, code in enumerate(dx):
                dx_rows.append((eid, code, "Primary" if k == 0 else "Secondary", None))

            # prescriptions
            drug_names = list(dict.fromkeys(d for c in dx for d in DIAGNOSIS_DRUGS[c]))
            if drug_names and rng.random() < 0.85:
                rx_id = len(rx_rows) + 1
                recent = when > AS_OF - timedelta(hours=6)
                dispensed = not recent and rng.random() < 0.97
                rx_rows.append((eid, doc, when + timedelta(minutes=5),
                                "Dispensed" if dispensed else "Pending",
                                rng.choice(pharmacists) if dispensed else None,
                                when + timedelta(minutes=rng.randrange(15, 120)) if dispensed else None))
                for name in drug_names:
                    drug_id, form, price = drugs[name]
                    dose, freq = DOSING[form]
                    days = rng.choice([3, 5, 7, 14, 30]) if form in ("Tablet", "Capsule") else rng.choice([1, 3, 5])
                    qty = days * (2 if freq == "Twice daily" else 3 if "Three" in freq else 1) \
                        if form in ("Tablet", "Capsule") else rng.randint(1, 4)
                    rx_item_rows.append((rx_id, drug_id, dose, freq, days, qty, qty if dispensed else 0))
                    if dispensed:
                        charges.append(("Drug", name, qty, price))

            # lab orders
            test_codes = list(dict.fromkeys(t for c in dx for t in DIAGNOSIS_TESTS[c]))
            for code in test_codes:
                if rng.random() > 0.75:
                    continue
                test_id, tname, low, high, price = tests[code]
                ordered = when + timedelta(minutes=rng.randrange(5, 30))
                lo_id = len(lab_order_rows) + 1
                pending = ordered > AS_OF - timedelta(hours=24)
                status = rng.choice(["Ordered", "Collected"]) if pending else \
                    rng.choices(["Completed", "Cancelled"], [98, 2])[0]
                lab_order_rows.append((eid, test_id, doc, ordered,
                                       "STAT" if etype == "Emergency" else rng.choices(["Routine", "Urgent"], [85, 15])[0],
                                       status))
                if status == "Cancelled":
                    continue
                charges.append(("LabTest", tname, 1, price))
                if status != "Completed":
                    continue
                relevant = code in DIAGNOSIS_TESTS[primary]
                abnormal = rng.random() < (0.6 if relevant else 0.12)
                if code == "GENO":
                    value, text, flag = None, patient_geno[pid], "Normal" if patient_geno[pid] in ("AA", "AS") else "High"
                elif code in QUALITATIVE:
                    neg, pos = QUALITATIVE[code]
                    value, text = None, rng.choice(pos if abnormal else neg)
                    flag = ("Critical" if code in ("HIV", "AFB") else "High") if abnormal else "Normal"
                else:
                    span = float(high - low) or float(high)
                    if abnormal:
                        go_low = code in ("FBC", "PCV") and rng.random() < 0.8
                        v = float(low) - rng.uniform(0.05, 0.5) * span if go_low else float(high) + rng.uniform(0.05, 1.2) * span
                    else:
                        v = rng.uniform(float(low), float(high))
                    v = max(v, 0.1)
                    value, text = Decimal(f"{v:.2f}"), None
                    if v < float(low):
                        flag = "Critical" if v < float(low) * 0.6 else "Low"
                    elif v > float(high):
                        flag = "Critical" if v > float(high) * 1.5 else "High"
                    else:
                        flag = "Normal"
                lab_result_rows.append((lo_id, value, text, flag, rng.choice(lab_scis),
                                        ordered + timedelta(hours=rng.randrange(1, 20))))
            enc_charges[eid] = charges

        # ---------------- invoices & payments ----------------
        inv_rows, item_rows, pay_rows = [], [], []
        cashiers = staff_by_role["Cashier"]
        enc_by_adm = {e[3]: eid for eid, e in enumerate(enc, start=1) if e[3] is not None}
        billable = []       # (issued_at, patient, encounter_id, admission_id, lines)
        for eid, e in enumerate(enc, start=1):
            if e[4] != "Inpatient":
                billable.append((e[5] + timedelta(minutes=rng.randrange(30, 180)), e[0], eid, None, enc_charges[eid]))
        for aid, a in enumerate(adm, start=1):
            if a[4] is None:
                continue                          # still admitted -> not billed yet
            nights = max(1, (a[4] - a[3]).days)
            eid = enc_by_adm[aid]
            lines = [("BedCharge", "Ward bed charge (per night)", nights, a[6])] + enc_charges[eid]
            billable.append((a[4], a[0], eid, aid, lines))
        billable.sort(key=lambda b: b[0])

        for inv_id, (issued, pid, eid, aid, lines) in enumerate(billable, start=1):
            total = sum(q * p for _, _, q, p in lines)
            outcome = "Paid" if issued < AS_OF - timedelta(days=2) and rng.random() < 0.90 else \
                rng.choices(["Paid", "Partial", "Unpaid"], [40, 35, 25])[0]
            for t, desc, q, p in lines:
                item_rows.append((inv_id, t, desc, q, p))
            cov = patient_cov[pid]
            insurer_share = (total * cov / 100).quantize(Decimal("0.01"))
            remaining = total
            if outcome != "Unpaid" and insurer_share > 0:
                pay_rows.append((inv_id, insurer_share, "Insurance", f"CLM-{inv_id:08d}",
                                 issued + timedelta(days=rng.randrange(1, 21)), rng.choice(cashiers)))
                remaining -= insurer_share
            if remaining > 0 and outcome != "Unpaid":
                amount = remaining if outcome == "Paid" else (remaining * Decimal(rng.uniform(0.2, 0.8))).quantize(Decimal("0.01"))
                if amount > 0:
                    pay_rows.append((inv_id, amount, rng.choices(["Cash", "POS", "Transfer", "Card"], [25, 35, 30, 10])[0],
                                     f"TXN-{inv_id:08d}", issued + timedelta(minutes=rng.randrange(1, 90)),
                                     rng.choice(cashiers)))
            inv_rows.append((f"INV-{issued.year}-{inv_id:07d}", pid, eid, aid, issued, outcome))

        # ---------------- load ----------------
        print("Loading with COPY ...")
        copy_rows(cur, "staff", ["staff_no", "first_name", "last_name", "role", "department_id", "email",
                                 "phone", "hire_date", "is_active"], staff_rows)
        copy_rows(cur, "doctors", ["staff_id", "specialty", "license_no", "consultation_fee"], doctor_rows)
        for dept, did in dept_id.items():
            head = doctors_by_dept[dept][0] if dept in doctors_by_dept else \
                next(i + 1 for i, s in enumerate(staff_rows) if s[4] == did)
            cur.execute("UPDATE hospital.departments SET head_staff_id = %s WHERE department_id = %s", (head, did))
        copy_rows(cur, "patients", ["hospital_no", "first_name", "last_name", "date_of_birth", "gender",
                                    "blood_group", "genotype", "phone", "email", "address", "state_of_origin",
                                    "next_of_kin_name", "next_of_kin_phone", "provider_id", "policy_no",
                                    "registered_at"], patient_rows)
        copy_rows(cur, "patient_allergies", ["patient_id", "allergen", "severity"], allergy_rows)
        copy_rows(cur, "appointments", ["patient_id", "doctor_id", "scheduled_start", "scheduled_end", "status",
                                        "reason", "booked_at"], (a[:7] for a in appts))
        copy_rows(cur, "admissions", ["patient_id", "bed_id", "admitting_doctor_id", "admitted_at",
                                      "discharged_at", "admission_reason"], (a[:6] for a in adm))
        cur.execute("UPDATE hospital.beds SET status = 'Occupied' WHERE bed_id = ANY(%s)", (bed_occupied,))
        copy_rows(cur, "encounters", ["patient_id", "doctor_id", "appointment_id", "admission_id", "encounter_type",
                                      "encounter_date", "chief_complaint", "clinical_notes", "temperature_c",
                                      "bp_systolic", "bp_diastolic", "pulse_bpm", "weight_kg"], enc_rows)
        copy_rows(cur, "diagnoses", ["encounter_id", "icd10_code", "diagnosis_type", "notes"], dx_rows)
        copy_rows(cur, "prescriptions", ["encounter_id", "prescribed_by", "prescribed_at", "status",
                                         "dispensed_by", "dispensed_at"], rx_rows)
        copy_rows(cur, "prescription_items", ["prescription_id", "drug_id", "dose", "frequency", "duration_days",
                                              "quantity", "dispensed_quantity"], rx_item_rows)
        copy_rows(cur, "lab_orders", ["encounter_id", "test_id", "ordered_by", "ordered_at", "priority", "status"],
                  lab_order_rows)
        copy_rows(cur, "lab_results", ["lab_order_id", "result_value", "result_text", "flag", "performed_by",
                                       "resulted_at"], lab_result_rows)
        copy_rows(cur, "invoices", ["invoice_no", "patient_id", "encounter_id", "admission_id", "issued_at", "status"],
                  inv_rows)
        copy_rows(cur, "invoice_items", ["invoice_id", "item_type", "description", "quantity", "unit_price"], item_rows)
        copy_rows(cur, "payments", ["invoice_id", "amount", "method", "reference", "paid_at", "received_by"], pay_rows)
        conn.commit()

        print("Updating planner statistics ...")
        conn.autocommit = True
        cur.execute("VACUUM ANALYZE")
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
