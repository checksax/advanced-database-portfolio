-- =====================================================================
-- St. Grace Specialist Hospital - Relational schema (PostgreSQL 16+)
-- Portfolio 1: Database Design and Modelling
--
-- Run as hospital_admin:
--   psql -U hospital_admin -d stgrace_hospital -f 01_schema.sql
--
-- Design notes
--   * Every table is in 3NF (most in BCNF); see ../normalisation.md.
--   * Surrogate keys (GENERATED ALWAYS AS IDENTITY) are used for entities;
--     natural keys (hospital_no, license_no, test_code, ICD-10 code) are
--     kept UNIQUE so the business can still identify rows.
--   * Money is NUMERIC(12,2) in Nigerian Naira (NGN).
--   * Business rules are enforced in the database (CHECK, UNIQUE,
--     EXCLUDE, partial unique indexes), not only in application code.
-- =====================================================================

DROP SCHEMA IF EXISTS hospital CASCADE;
CREATE SCHEMA hospital;
SET search_path TO hospital, public;

-- ---------------------------------------------------------------------
-- 1. Organisation and staff
-- ---------------------------------------------------------------------
CREATE TABLE departments (
    department_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            VARCHAR(80)  NOT NULL UNIQUE,
    location        VARCHAR(80)  NOT NULL,
    phone_ext       VARCHAR(10),
    head_staff_id   INT          -- FK added after staff exists (circular reference)
);

CREATE TABLE staff (
    staff_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    staff_no        VARCHAR(12)  NOT NULL UNIQUE,
    first_name      VARCHAR(50)  NOT NULL,
    last_name       VARCHAR(50)  NOT NULL,
    role            VARCHAR(20)  NOT NULL
                    CHECK (role IN ('Doctor','Nurse','LabScientist','Pharmacist',
                                    'Cashier','RecordsOfficer','Administrator')),
    department_id   INT          NOT NULL REFERENCES departments(department_id),
    email           VARCHAR(120) NOT NULL UNIQUE,
    phone           VARCHAR(20),
    hire_date       DATE         NOT NULL DEFAULT CURRENT_DATE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

ALTER TABLE departments
    ADD CONSTRAINT fk_department_head
    FOREIGN KEY (head_staff_id) REFERENCES staff(staff_id)
    DEFERRABLE INITIALLY DEFERRED;

-- Specialisation of staff (ISA): only doctors have a licence, specialty and fee.
CREATE TABLE doctors (
    staff_id          INT PRIMARY KEY REFERENCES staff(staff_id) ON DELETE CASCADE,
    specialty         VARCHAR(60)   NOT NULL,
    license_no        VARCHAR(20)   NOT NULL UNIQUE,
    consultation_fee  NUMERIC(12,2) NOT NULL CHECK (consultation_fee >= 0)
);

-- ---------------------------------------------------------------------
-- 2. Patients
-- ---------------------------------------------------------------------
CREATE TABLE insurance_providers (
    provider_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name              VARCHAR(100) NOT NULL UNIQUE,
    coverage_percent  NUMERIC(5,2) NOT NULL CHECK (coverage_percent BETWEEN 0 AND 100),
    phone             VARCHAR(20)
);

CREATE TABLE patients (
    patient_id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hospital_no         VARCHAR(12)  NOT NULL UNIQUE,      -- e.g. SGH-0000123
    first_name          VARCHAR(50)  NOT NULL,
    last_name           VARCHAR(50)  NOT NULL,
    date_of_birth       DATE         NOT NULL CHECK (date_of_birth <= CURRENT_DATE),
    gender              CHAR(1)      NOT NULL CHECK (gender IN ('M','F')),
    blood_group         VARCHAR(3)   CHECK (blood_group IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')),
    genotype            VARCHAR(2)   CHECK (genotype IN ('AA','AS','SS','AC','SC')),
    phone               VARCHAR(20)  NOT NULL,
    email               VARCHAR(120),
    address             VARCHAR(200),
    state_of_origin     VARCHAR(40),
    next_of_kin_name    VARCHAR(100),
    next_of_kin_phone   VARCHAR(20),
    provider_id         INT          REFERENCES insurance_providers(provider_id),
    policy_no           VARCHAR(30),
    registered_at       TIMESTAMP    NOT NULL DEFAULT now(),
    -- a policy number only makes sense with a provider, and vice versa
    CONSTRAINT chk_insurance_pair CHECK ((provider_id IS NULL) = (policy_no IS NULL))
);

-- Multi-valued attribute moved to its own table (1NF).
CREATE TABLE patient_allergies (
    patient_id  INT         NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    allergen    VARCHAR(60) NOT NULL,
    severity    VARCHAR(10) NOT NULL CHECK (severity IN ('Mild','Moderate','Severe')),
    PRIMARY KEY (patient_id, allergen)
);

-- ---------------------------------------------------------------------
-- 3. Appointments and clinical encounters
-- ---------------------------------------------------------------------
CREATE TABLE appointments (
    appointment_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id       INT          NOT NULL REFERENCES patients(patient_id),
    doctor_id        INT          NOT NULL REFERENCES doctors(staff_id),
    scheduled_start  TIMESTAMP    NOT NULL,
    scheduled_end    TIMESTAMP    NOT NULL,
    status           VARCHAR(12)  NOT NULL DEFAULT 'Scheduled'
                     CHECK (status IN ('Scheduled','CheckedIn','Completed','Cancelled','NoShow')),
    reason           VARCHAR(200),
    booked_at        TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT chk_appt_times CHECK (scheduled_end > scheduled_start),
    -- A doctor can never have two overlapping, non-cancelled appointments.
    CONSTRAINT no_doctor_double_booking EXCLUDE USING gist (
        doctor_id WITH =,
        tsrange(scheduled_start, scheduled_end) WITH &&
    ) WHERE (status <> 'Cancelled')
);

CREATE TABLE wards (
    ward_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           VARCHAR(60)   NOT NULL UNIQUE,
    ward_type      VARCHAR(20)   NOT NULL
                   CHECK (ward_type IN ('General','Private','ICU','Maternity','Paediatric','Surgical')),
    department_id  INT           NOT NULL REFERENCES departments(department_id),
    daily_rate     NUMERIC(12,2) NOT NULL CHECK (daily_rate >= 0)
);

CREATE TABLE beds (
    bed_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ward_id     INT         NOT NULL REFERENCES wards(ward_id),
    bed_number  VARCHAR(10) NOT NULL,
    status      VARCHAR(12) NOT NULL DEFAULT 'Available'
                CHECK (status IN ('Available','Occupied','Maintenance')),
    UNIQUE (ward_id, bed_number)
);

CREATE TABLE admissions (
    admission_id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id           INT       NOT NULL REFERENCES patients(patient_id),
    bed_id               INT       NOT NULL REFERENCES beds(bed_id),
    admitting_doctor_id  INT       NOT NULL REFERENCES doctors(staff_id),
    admitted_at          TIMESTAMP NOT NULL DEFAULT now(),
    discharged_at        TIMESTAMP,
    admission_reason     VARCHAR(200) NOT NULL,
    discharge_summary    TEXT,
    CONSTRAINT chk_discharge_after_admit
        CHECK (discharged_at IS NULL OR discharged_at > admitted_at)
);
-- A bed holds at most one current patient; a patient is in at most one bed.
CREATE UNIQUE INDEX uq_active_admission_bed     ON admissions(bed_id)     WHERE discharged_at IS NULL;
CREATE UNIQUE INDEX uq_active_admission_patient ON admissions(patient_id) WHERE discharged_at IS NULL;

CREATE TABLE encounters (
    encounter_id     INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id       INT         NOT NULL REFERENCES patients(patient_id),
    doctor_id        INT         NOT NULL REFERENCES doctors(staff_id),
    appointment_id   INT         UNIQUE REFERENCES appointments(appointment_id),
    admission_id     INT         REFERENCES admissions(admission_id),
    encounter_type   VARCHAR(12) NOT NULL
                     CHECK (encounter_type IN ('Outpatient','Inpatient','Emergency')),
    encounter_date   TIMESTAMP   NOT NULL DEFAULT now(),
    chief_complaint  VARCHAR(200),
    clinical_notes   TEXT,
    -- vital signs captured at the encounter
    temperature_c    NUMERIC(4,1) CHECK (temperature_c BETWEEN 30 AND 45),
    bp_systolic      SMALLINT     CHECK (bp_systolic BETWEEN 50 AND 260),
    bp_diastolic     SMALLINT     CHECK (bp_diastolic BETWEEN 30 AND 160),
    pulse_bpm        SMALLINT     CHECK (pulse_bpm BETWEEN 20 AND 250),
    weight_kg        NUMERIC(5,1) CHECK (weight_kg BETWEEN 0.3 AND 400),
    CONSTRAINT chk_inpatient_has_admission
        CHECK (encounter_type <> 'Inpatient' OR admission_id IS NOT NULL)
);

CREATE TABLE icd10_codes (
    code         VARCHAR(8)   PRIMARY KEY,          -- e.g. B54, E11.9
    description  VARCHAR(200) NOT NULL,
    category     VARCHAR(80)  NOT NULL
);

CREATE TABLE diagnoses (
    diagnosis_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id    INT         NOT NULL REFERENCES encounters(encounter_id) ON DELETE CASCADE,
    icd10_code      VARCHAR(8)  NOT NULL REFERENCES icd10_codes(code),
    diagnosis_type  VARCHAR(10) NOT NULL DEFAULT 'Primary'
                    CHECK (diagnosis_type IN ('Primary','Secondary')),
    notes           VARCHAR(300),
    UNIQUE (encounter_id, icd10_code)
);

-- ---------------------------------------------------------------------
-- 4. Pharmacy
-- ---------------------------------------------------------------------
CREATE TABLE drugs (
    drug_id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    generic_name    VARCHAR(80)   NOT NULL,
    brand_name      VARCHAR(80),
    dosage_form     VARCHAR(20)   NOT NULL
                    CHECK (dosage_form IN ('Tablet','Capsule','Syrup','Injection','Infusion','Cream','Inhaler','Suspension')),
    strength        VARCHAR(30)   NOT NULL,
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    stock_quantity  INT           NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    reorder_level   INT           NOT NULL DEFAULT 50 CHECK (reorder_level >= 0),
    UNIQUE (generic_name, dosage_form, strength)
);

CREATE TABLE prescriptions (
    prescription_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id     INT         NOT NULL REFERENCES encounters(encounter_id),
    prescribed_by    INT         NOT NULL REFERENCES doctors(staff_id),
    prescribed_at    TIMESTAMP   NOT NULL DEFAULT now(),
    status           VARCHAR(12) NOT NULL DEFAULT 'Pending'
                     CHECK (status IN ('Pending','Partial','Dispensed','Cancelled')),
    dispensed_by     INT         REFERENCES staff(staff_id),
    dispensed_at     TIMESTAMP
);

CREATE TABLE prescription_items (
    prescription_id     INT          NOT NULL REFERENCES prescriptions(prescription_id) ON DELETE CASCADE,
    drug_id             INT          NOT NULL REFERENCES drugs(drug_id),
    dose                VARCHAR(30)  NOT NULL,           -- e.g. '500 mg'
    frequency           VARCHAR(30)  NOT NULL,           -- e.g. 'Twice daily'
    duration_days       SMALLINT     NOT NULL CHECK (duration_days > 0),
    quantity            INT          NOT NULL CHECK (quantity > 0),
    dispensed_quantity  INT          NOT NULL DEFAULT 0,
    PRIMARY KEY (prescription_id, drug_id),
    CONSTRAINT chk_dispensed_range CHECK (dispensed_quantity BETWEEN 0 AND quantity)
);

-- ---------------------------------------------------------------------
-- 5. Laboratory
-- ---------------------------------------------------------------------
CREATE TABLE lab_tests (
    test_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    test_code     VARCHAR(12)   NOT NULL UNIQUE,
    name          VARCHAR(100)  NOT NULL,
    sample_type   VARCHAR(20)   NOT NULL CHECK (sample_type IN ('Blood','Urine','Stool','Swab','Sputum','CSF')),
    unit          VARCHAR(20),
    normal_low    NUMERIC(10,2),
    normal_high   NUMERIC(10,2),
    price         NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    CONSTRAINT chk_normal_range CHECK (normal_low IS NULL OR normal_high IS NULL OR normal_low <= normal_high)
);

CREATE TABLE lab_orders (
    lab_order_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id  INT         NOT NULL REFERENCES encounters(encounter_id),
    test_id       INT         NOT NULL REFERENCES lab_tests(test_id),
    ordered_by    INT         NOT NULL REFERENCES doctors(staff_id),
    ordered_at    TIMESTAMP   NOT NULL DEFAULT now(),
    priority      VARCHAR(8)  NOT NULL DEFAULT 'Routine' CHECK (priority IN ('Routine','Urgent','STAT')),
    status        VARCHAR(12) NOT NULL DEFAULT 'Ordered'
                  CHECK (status IN ('Ordered','Collected','Completed','Cancelled'))
);

-- 1:1 with lab_orders; kept separate because a result only exists once the
-- test is performed (avoids a block of NULL columns on every pending order).
CREATE TABLE lab_results (
    lab_order_id  INT PRIMARY KEY REFERENCES lab_orders(lab_order_id) ON DELETE CASCADE,
    result_value  NUMERIC(10,2),
    result_text   VARCHAR(300),
    flag          VARCHAR(8)  NOT NULL DEFAULT 'Normal'
                  CHECK (flag IN ('Normal','Low','High','Critical')),
    performed_by  INT         NOT NULL REFERENCES staff(staff_id),
    resulted_at   TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT chk_result_present CHECK (result_value IS NOT NULL OR result_text IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- 6. Billing
-- ---------------------------------------------------------------------
CREATE TABLE invoices (
    invoice_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_no    VARCHAR(16) NOT NULL UNIQUE,
    patient_id    INT         NOT NULL REFERENCES patients(patient_id),
    encounter_id  INT         REFERENCES encounters(encounter_id),
    admission_id  INT         REFERENCES admissions(admission_id),
    issued_at     TIMESTAMP   NOT NULL DEFAULT now(),
    status        VARCHAR(10) NOT NULL DEFAULT 'Unpaid'
                  CHECK (status IN ('Unpaid','Partial','Paid','Void')),
    CONSTRAINT chk_invoice_source CHECK (encounter_id IS NOT NULL OR admission_id IS NOT NULL)
);

CREATE TABLE invoice_items (
    invoice_item_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id       INT           NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    item_type        VARCHAR(12)   NOT NULL
                     CHECK (item_type IN ('Consultation','Drug','LabTest','BedCharge','Procedure')),
    description      VARCHAR(150)  NOT NULL,
    quantity         INT           NOT NULL CHECK (quantity > 0),
    unit_price       NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total       NUMERIC(14,2) GENERATED ALWAYS AS (quantity * unit_price) STORED
);

CREATE TABLE payments (
    payment_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id   INT           NOT NULL REFERENCES invoices(invoice_id),
    amount       NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    method       VARCHAR(12)   NOT NULL CHECK (method IN ('Cash','Card','Transfer','Insurance','POS')),
    reference    VARCHAR(40)   UNIQUE,
    paid_at      TIMESTAMP     NOT NULL DEFAULT now(),
    received_by  INT           NOT NULL REFERENCES staff(staff_id)
);

-- ---------------------------------------------------------------------
-- 7. Foreign-key indexes
-- PostgreSQL does not index FK columns automatically. These are the
-- baseline indexes; Portfolio 2 adds and measures workload-specific ones.
-- ---------------------------------------------------------------------
CREATE INDEX idx_staff_department     ON staff(department_id);
CREATE INDEX idx_appt_patient         ON appointments(patient_id);
CREATE INDEX idx_admissions_patient   ON admissions(patient_id);
CREATE INDEX idx_encounters_patient   ON encounters(patient_id);
CREATE INDEX idx_diagnoses_encounter  ON diagnoses(encounter_id);
CREATE INDEX idx_rx_encounter         ON prescriptions(encounter_id);
CREATE INDEX idx_rx_items_drug        ON prescription_items(drug_id);
CREATE INDEX idx_lab_orders_encounter ON lab_orders(encounter_id);
CREATE INDEX idx_invoices_patient     ON invoices(patient_id);
CREATE INDEX idx_invoice_items_inv    ON invoice_items(invoice_id);
CREATE INDEX idx_payments_invoice     ON payments(invoice_id);

-- ---------------------------------------------------------------------
-- 8. Views (derived data is computed, not stored - keeps 3NF)
-- ---------------------------------------------------------------------
CREATE VIEW v_invoice_balance AS
SELECT i.invoice_id,
       i.invoice_no,
       i.patient_id,
       COALESCE(it.total, 0)                        AS total_amount,
       COALESCE(p.paid, 0)                          AS amount_paid,
       COALESCE(it.total, 0) - COALESCE(p.paid, 0)  AS balance_due,
       i.status
FROM invoices i
LEFT JOIN (SELECT invoice_id, SUM(line_total) AS total FROM invoice_items GROUP BY invoice_id) it
       ON it.invoice_id = i.invoice_id
LEFT JOIN (SELECT invoice_id, SUM(amount) AS paid FROM payments GROUP BY invoice_id) p
       ON p.invoice_id = i.invoice_id;

CREATE VIEW v_patient_summary AS
SELECT p.patient_id,
       p.hospital_no,
       p.first_name || ' ' || p.last_name             AS full_name,
       date_part('year', age(p.date_of_birth))::int   AS age_years,
       p.gender,
       p.blood_group,
       p.genotype,
       ip.name                                        AS insurer
FROM patients p
LEFT JOIN insurance_providers ip ON ip.provider_id = p.provider_id;

CREATE VIEW v_bed_occupancy AS
SELECT w.ward_id,
       w.name AS ward,
       w.ward_type,
       COUNT(*)                                       AS total_beds,
       COUNT(*) FILTER (WHERE b.status = 'Occupied')  AS occupied,
       ROUND(100.0 * COUNT(*) FILTER (WHERE b.status = 'Occupied') / COUNT(*), 1) AS occupancy_pct
FROM wards w
JOIN beds b ON b.ward_id = w.ward_id
GROUP BY w.ward_id, w.name, w.ward_type;
