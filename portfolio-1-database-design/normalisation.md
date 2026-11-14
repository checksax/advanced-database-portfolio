# Normalisation — St. Grace Specialist Hospital

This section shows how the schema was reached. It starts from the paper **outpatient
prescription slip** the hospital used before computerisation and normalises it step by
step to 3NF and BCNF.

## 0. Unnormalised form (UNF)

One slip per visit, copied into a single table as it appears on paper:

| slip_no | visit_date | hospital_no | patient_name | patient_phone | insurer | insurer_coverage | doctor_licence | doctor_name | doctor_dept | diagnoses | drugs (name, strength, price, dose, qty) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1001 | 2026-03-02 | SGH-0000123 | Ngozi Okafor | 0803… | Hygeia HMO | 80% | MDCN/12345 | Dr. Bello | General Medicine | B54 Malaria; D64.9 Anaemia | Coartem 20/120mg ₦350 1tab×12; Ferrous Sulphate 200mg ₦10 1tab×30 |
| 1002 | 2026-03-02 | SGH-0000456 | Tunde Adeyemi | 0806… | — | — | MDCN/12345 | Dr. Bello | General Medicine | I10 Hypertension | Amlodipine 5mg ₦45 1tab×30 |

**Problems with UNF**
- **Repeating groups:** `diagnoses` and `drugs` hold lists, so "every patient on Coartem" can't be queried without string parsing.
- **Update anomaly:** if the price of Coartem changes, every slip that mentions it must be edited.
- **Insertion anomaly:** a new drug can't be recorded until someone is prescribed it.
- **Deletion anomaly:** deleting slip 1002 deletes the only record that Amlodipine costs ₦45.

## 1. First Normal Form (1NF): atomic values, no repeating groups

Each list item is split into its own row, so the key becomes `(slip_no, drug_name)` for
drugs and `(slip_no, icd_code)` for diagnoses. Diagnoses and drugs are independent
multi-valued facts about a slip. Keeping them in one table would create a
diagnosis × drug cross product (a multivalued dependency, which 4NF also forbids), so
they are split into two relations from the start:

**R1 SLIP_DRUG** (`slip_no`, `drug_name`, visit_date, hospital_no, patient_name, patient_phone, insurer, insurer_coverage, doctor_licence, doctor_name, doctor_dept, strength, unit_price, dose, qty)

**R2 SLIP_DIAGNOSIS** (`slip_no`, `icd_code`, icd_description)

All values are now atomic, but redundancy is still severe. Patient and doctor details
repeat on every drug line.

## 2. Second Normal Form (2NF): remove partial dependencies

Functional dependencies (FDs) in R1, with key `{slip_no, drug_name}`:

| FD | Type |
|---|---|
| slip_no → visit_date, hospital_no, doctor_licence | **partial** (depends on part of the key) |
| drug_name → strength, unit_price | **partial** |
| slip_no, drug_name → dose, qty | full |

In R2, `icd_code → icd_description` is also partial.

Decompose so that every non-key attribute depends on the **whole** key:

- **SLIP** (`slip_no`, visit_date, hospital_no, patient_name, patient_phone, insurer, insurer_coverage, doctor_licence, doctor_name, doctor_dept)
- **DRUG** (`drug_name`, strength, unit_price)
- **SLIP_ITEM** (`slip_no`, `drug_name`, dose, qty)
- **ICD** (`icd_code`, icd_description)
- **SLIP_DIAGNOSIS** (`slip_no`, `icd_code`)

## 3. Third Normal Form (3NF): remove transitive dependencies

SLIP still contains transitive dependencies (non-key → non-key):

| Transitive chain | Fix |
|---|---|
| slip_no → hospital_no → patient_name, patient_phone, insurer | move to **PATIENT** |
| hospital_no → insurer → insurer_coverage | move to **INSURANCE_PROVIDER** |
| slip_no → doctor_licence → doctor_name, doctor_dept | move to **DOCTOR** / **STAFF** |
| doctor_dept → dept location, phone | move to **DEPARTMENT** |

Result:

- **PATIENT** (`hospital_no`, name, phone, insurer_id)
- **INSURANCE_PROVIDER** (`insurer_id`, name, coverage_percent)
- **STAFF** (`staff_id`, name, department_id) + **DOCTOR** (`staff_id`, licence_no, specialty)
- **DEPARTMENT** (`department_id`, name, location)
- **ENCOUNTER** (formerly SLIP) (`encounter_id`, visit_date, patient_id, doctor_id)
- **PRESCRIPTION** (`prescription_id`, encounter_id, …) + **PRESCRIPTION_ITEM** (`prescription_id`, `drug_id`, dose, qty)
- **DRUG** (`drug_id`, generic_name, strength, unit_price)
- **DIAGNOSIS** (`encounter_id`, `icd10_code`) + **ICD10_CODE** (`code`, description)

These map one-to-one onto the tables in `sql/01_schema.sql`.

## 4. Boyce–Codd Normal Form (BCNF) check

A relation is in BCNF if, for every non-trivial FD X → Y, X is a superkey.

| Table | Candidate keys | Determinants | BCNF? |
|---|---|---|---|
| patients | patient_id, hospital_no | patient_id, hospital_no | ✅ both determinants are keys |
| staff | staff_id, staff_no, email | the same three | ✅ |
| doctors | staff_id, license_no | the same two | ✅ |
| drugs | drug_id, (generic_name, dosage_form, strength) | the same two | ✅ |
| beds | bed_id, (ward_id, bed_number) | the same two | ✅ |
| prescription_items | (prescription_id, drug_id) | key only | ✅ |
| lab_tests | test_id, test_code | the same two | ✅ |
| appointments | appointment_id | key only* | ✅ |

\* `(doctor_id, time range)` behaves like a key for active appointments, but that rule is
an exclusion (overlap) constraint, not equality, so it isn't a functional dependency.

## 5. Deliberate, controlled exceptions

Normalisation is a tool, not a goal. Three columns go against strict normalisation on purpose:

| Column | Why it is kept | How it is kept consistent |
|---|---|---|
| `beds.status` ('Occupied') | Derivable from "an active admission exists". It is stored because bed allocation is a high-contention operation: in Portfolio 3 we lock the bed row (`SELECT … FOR UPDATE`) to stop two nurses assigning the same bed. | Updated in the same transaction as the admission; `04_data_profile.sql` checks for disagreement. |
| `invoice_items.unit_price` | Looks like a copy of `drugs.unit_price`, but it is the **price at the time of billing**. Prices change, and old invoices must not. That makes it a different fact. | Written once at billing time. |
| `invoice_items.line_total` | Derived (`quantity × unit_price`). | `GENERATED ALWAYS … STORED`: the database computes it, so it can never be wrong. |

Invoice totals and balances are **not** stored. They are computed in the view
`v_invoice_balance`, so they can't drift out of step with the items and payments.
