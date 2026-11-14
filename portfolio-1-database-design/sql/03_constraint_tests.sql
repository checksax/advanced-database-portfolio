-- =====================================================================
-- St. Grace Specialist Hospital - Constraint verification
-- Each test tries to break one business rule. Every test runs inside
-- BEGIN ... ROLLBACK, so the database is left unchanged.
-- EXPECTED: each "SHOULD FAIL" step prints an ERROR naming the constraint.
--
--   psql -U hospital_admin -d stgrace_hospital -f 03_constraint_tests.sql > ../evidence/constraint_tests_output.txt 2>&1
-- =====================================================================
SET search_path TO hospital, public;
\set ON_ERROR_STOP off

\echo '=== T1  Doctor double-booking (EXCLUDE constraint no_doctor_double_booking) ==='
BEGIN;
INSERT INTO appointments (patient_id, doctor_id, scheduled_start, scheduled_end)
VALUES (1, (SELECT min(staff_id) FROM doctors), '2030-01-07 09:00', '2030-01-07 09:30');
\echo '--- overlapping slot for the same doctor, SHOULD FAIL:'
INSERT INTO appointments (patient_id, doctor_id, scheduled_start, scheduled_end)
VALUES (2, (SELECT min(staff_id) FROM doctors), '2030-01-07 09:15', '2030-01-07 09:45');
ROLLBACK;

\echo '=== T2  Cancelled appointments free the slot (partial EXCLUDE) - SHOULD SUCCEED ==='
BEGIN;
INSERT INTO appointments (patient_id, doctor_id, scheduled_start, scheduled_end, status)
VALUES (1, (SELECT min(staff_id) FROM doctors), '2030-01-07 10:00', '2030-01-07 10:30', 'Cancelled');
INSERT INTO appointments (patient_id, doctor_id, scheduled_start, scheduled_end)
VALUES (2, (SELECT min(staff_id) FROM doctors), '2030-01-07 10:00', '2030-01-07 10:30');
SELECT 'T2 passed: slot re-booked after cancellation' AS result;
ROLLBACK;

\echo '=== T3  Appointment ends before it starts (CHECK chk_appt_times) - SHOULD FAIL ==='
BEGIN;
INSERT INTO appointments (patient_id, doctor_id, scheduled_start, scheduled_end)
VALUES (1, (SELECT min(staff_id) FROM doctors), '2030-01-08 11:00', '2030-01-08 10:00');
ROLLBACK;

\echo '=== T4  Two current patients in one bed (partial UNIQUE uq_active_admission_bed) - SHOULD FAIL ==='
BEGIN;
INSERT INTO admissions (patient_id, bed_id, admitting_doctor_id, admission_reason)
SELECT 3, bed_id, (SELECT min(staff_id) FROM doctors), 'Test'
FROM admissions WHERE discharged_at IS NULL LIMIT 1;
ROLLBACK;

\echo '=== T5  Drug stock cannot go negative (CHECK stock_quantity >= 0) - SHOULD FAIL ==='
BEGIN;
UPDATE drugs SET stock_quantity = stock_quantity - 1000000 WHERE generic_name = 'Paracetamol';
ROLLBACK;

\echo '=== T6  Dispense more than prescribed (CHECK chk_dispensed_range) - SHOULD FAIL ==='
BEGIN;
UPDATE prescription_items SET dispensed_quantity = quantity + 1
WHERE (prescription_id, drug_id) = (SELECT prescription_id, drug_id FROM prescription_items LIMIT 1);
ROLLBACK;

\echo '=== T7  Duplicate hospital number (UNIQUE) - SHOULD FAIL ==='
BEGIN;
INSERT INTO patients (hospital_no, first_name, last_name, date_of_birth, gender, phone)
VALUES ('SGH-0000001', 'Test', 'Duplicate', '1990-01-01', 'M', '08030000000');
ROLLBACK;

\echo '=== T8  Invalid genotype value (CHECK) - SHOULD FAIL ==='
BEGIN;
INSERT INTO patients (hospital_no, first_name, last_name, date_of_birth, gender, genotype, phone)
VALUES ('SGH-TEST001', 'Test', 'Genotype', '1990-01-01', 'F', 'XX', '08030000000');
ROLLBACK;

\echo '=== T9  Policy number without an insurer (CHECK chk_insurance_pair) - SHOULD FAIL ==='
BEGIN;
INSERT INTO patients (hospital_no, first_name, last_name, date_of_birth, gender, phone, policy_no)
VALUES ('SGH-TEST002', 'Test', 'Policy', '1990-01-01', 'F', '08030000000', 'POL-00000000');
ROLLBACK;

\echo '=== T10 Diagnosis with unknown ICD-10 code (FOREIGN KEY) - SHOULD FAIL ==='
BEGIN;
INSERT INTO diagnoses (encounter_id, icd10_code) VALUES (1, 'ZZZ.9');
ROLLBACK;

\echo '=== T11 Inpatient encounter with no admission (CHECK chk_inpatient_has_admission) - SHOULD FAIL ==='
BEGIN;
INSERT INTO encounters (patient_id, doctor_id, encounter_type)
VALUES (1, (SELECT min(staff_id) FROM doctors), 'Inpatient');
ROLLBACK;

\echo '=== T12 Zero or negative payment (CHECK amount > 0) - SHOULD FAIL ==='
BEGIN;
INSERT INTO payments (invoice_id, amount, method, received_by)
VALUES (1, -500, 'Cash', (SELECT min(staff_id) FROM staff WHERE role = 'Cashier'));
ROLLBACK;

\echo '=== T13 Lab result with no value at all (CHECK chk_result_present) - SHOULD FAIL ==='
BEGIN;
INSERT INTO lab_results (lab_order_id, performed_by)
SELECT lo.lab_order_id, (SELECT min(staff_id) FROM staff WHERE role = 'LabScientist')
FROM lab_orders lo LEFT JOIN lab_results r USING (lab_order_id)
WHERE r.lab_order_id IS NULL LIMIT 1;
ROLLBACK;

\echo '=== T14 Delete a patient who has clinical history (FK RESTRICT) - SHOULD FAIL ==='
BEGIN;
DELETE FROM patients WHERE patient_id = (SELECT patient_id FROM encounters LIMIT 1);
ROLLBACK;

\echo '=== T15 Generated column line_total is computed, not typed - SHOULD SUCCEED ==='
SELECT invoice_item_id, quantity, unit_price, line_total, quantity * unit_price AS check_value
FROM invoice_items ORDER BY invoice_item_id LIMIT 3;

\echo '=== Done ==='
