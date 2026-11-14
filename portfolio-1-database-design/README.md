# Portfolio 1 — Database Design and Modelling

**Case study:** St. Grace Specialist Hospital, Hospital Management System
**DBMS:** PostgreSQL 18

## Contents

| File | What it shows |
|---|---|
| `er-diagram.md` | ER diagram (crow's-foot) and the modelling decisions behind it |
| `normalisation.md` | UNF → 1NF → 2NF → 3NF → BCNF worked example, plus deliberate exceptions |
| `sql/00_create_database.sql` | Creates the application role, database and extensions (run once as `postgres`) |
| `sql/01_schema.sql` | 22 tables, keys, constraints, indexes, views |
| `sql/02_reference_data.sql` | Departments, insurers, ICD-10 codes, drugs, lab tests, wards, beds |
| `sql/03_constraint_tests.sql` | 15 tests that try to break business rules and show the database refusing |
| `sql/04_data_profile.sql` | Row counts, constraint inventory, consistency checks |
| `../data-generator/` | Python generator: ~1 million consistent, fictitious rows |
| `evidence/` | Captured output from the scripts above |
| `screenshots/` | pgAdmin screenshots (schema tree, ERD tool, sample data) |

## Entities (22 tables)

| Area | Tables |
|---|---|
| Organisation | `departments`, `staff`, `doctors` |
| Patients | `patients`, `patient_allergies`, `insurance_providers` |
| Scheduling & care | `appointments`, `encounters`, `diagnoses`, `icd10_codes` |
| Inpatient | `wards`, `beds`, `admissions` |
| Pharmacy | `drugs`, `prescriptions`, `prescription_items` |
| Laboratory | `lab_tests`, `lab_orders`, `lab_results` |
| Billing | `invoices`, `invoice_items`, `payments` |

## Keys and constraints

- **Primary keys:** surrogate `INT GENERATED ALWAYS AS IDENTITY` keys, plus composite keys on
  `patient_allergies(patient_id, allergen)` and `prescription_items(prescription_id, drug_id)`.
- **Alternate (natural) keys** kept `UNIQUE`: `hospital_no`, `staff_no`, `license_no`, `test_code`,
  `invoice_no`, `email`, `(ward_id, bed_number)`.
- **Foreign keys:** every relationship. `ON DELETE CASCADE` is used only for weak or child entities
  (allergies, prescription items, invoice items, lab results). Everything else is `RESTRICT`, so
  clinical history can never be orphaned or silently deleted.
- **CHECK constraints:** value domains (role, gender, genotype, blood group, status values),
  ranges (vital signs, prices ≥ 0, stock ≥ 0, coverage 0–100), and cross-column rules
  (`scheduled_end > scheduled_start`, `dispensed_quantity ≤ quantity`, insurer and policy number
  given together, inpatient encounter must have an admission).
- **Advanced constraints:**
  - `EXCLUDE USING gist (doctor_id WITH =, tsrange(start, end) WITH &&) WHERE status <> 'Cancelled'`
    stops a doctor from being double-booked, even for partly overlapping times. A plain
    `UNIQUE` can't express this.
  - **Partial unique indexes:** one *current* admission per bed and per patient, while keeping the
    full admission history.
  - **Generated column:** `invoice_items.line_total`.
  - **Deferrable FK:** department ↔ head of department (circular reference).

## How to run

```powershell
# once, as the PostgreSQL superuser (asks for your postgres password)
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -f sql\00_create_database.sql

# rebuild schema, load data, capture evidence
pip install -r ..\data-generator\requirements.txt
.\run_portfolio1.ps1
```

The full load takes about 8 minutes on a laptop.

## Results (run on 3 Nov 2026, PostgreSQL 18)

**Data loaded:** about 2.6 million rows across 22 tables (`evidence/data_load_output.txt`).

| Table | Rows | Table | Rows |
|---|---:|---|---:|
| invoice_items | 667,013 | encounters | 172,743 |
| prescription_items | 282,410 | invoices | 172,707 |
| payments | 227,852 | prescriptions | 140,054 |
| lab_orders | 214,900 | patients | 50,000 |
| lab_results | 210,253 | admissions | 9,737 |
| diagnoses | 204,659 | patient_allergies | 6,838 |
| appointments | 201,328 | beds / wards | 108 / 6 |

**Constraints in force** (`evidence/data_profile_output.txt`): 116 NOT NULL, 45 CHECK,
34 FOREIGN KEY, 22 PRIMARY KEY, 14 UNIQUE, 1 EXCLUDE, plus 2 partial unique indexes.

**Constraint tests** (`evidence/constraint_tests_output.txt`): all 15 behaved as designed.
The 13 "should fail" cases were rejected, each naming the right constraint, and the 2
"should succeed" cases passed. For example:

```
ERROR:  conflicting key value violates exclusion constraint "no_doctor_double_booking"
DETAIL: Key (doctor_id, tsrange(scheduled_start, scheduled_end))=(1, ["2030-01-07 09:15","2030-01-07 09:45"))
        conflicts with existing key (doctor_id, tsrange(...))=(1, ["2030-01-07 09:00","2030-01-07 09:30")).
```

**Consistency checks:** 0 violations on all six (no overlapping appointments, no
overlapping bed stays, bed status matches admissions, no overpaid invoices, no "Paid"
invoice with a balance, no obstetric diagnosis on a male patient).
