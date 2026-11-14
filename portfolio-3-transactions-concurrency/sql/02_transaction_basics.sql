-- =====================================================================
-- St. Grace Specialist Hospital - Portfolio 3: single-session transactions
--   T1 COMMIT                - a dispensing transaction made permanent
--   T2 Automatic ROLLBACK    - a failure halfway through undoes the earlier steps
--   T3 Explicit ROLLBACK     - a user cancels their own mistake
--   T4 SAVEPOINT             - discharge continues after an optional step fails
--   T5 MVCC row versions     - xmin/xmax before, during and after a transaction
--   T6 Statement atomicity   - one UPDATE touching two rows is all-or-nothing
--
-- Uses demo rows created in SETUP and removed in CLEANUP.
--   psql -U hospital_admin -d stgrace_hospital -f 02_transaction_basics.sql
--        > ../results/transaction_basics_output.txt 2>&1
-- =====================================================================
SET search_path TO hospital, public;
\set ON_ERROR_STOP off
\pset footer off

\echo '================ SETUP: demo drugs, prescriptions and an admission ================'
-- Paracetamol is inserted first so it gets the lower drug_id and is processed first.
INSERT INTO drugs (generic_name, dosage_form, strength, unit_price, stock_quantity)
VALUES ('DEMO Paracetamol', 'Tablet', '500 mg', 20, 100) RETURNING drug_id AS para_id \gset
INSERT INTO drugs (generic_name, dosage_form, strength, unit_price, stock_quantity)
VALUES ('DEMO Artesunate', 'Injection', '60 mg', 2500, 10) RETURNING drug_id AS art_id \gset

SELECT encounter_id AS enc_id, doctor_id AS doc_id
FROM encounters WHERE encounter_type = 'Outpatient' ORDER BY encounter_id LIMIT 1 \gset
SELECT min(staff_id) AS pharm_id   FROM staff WHERE role = 'Pharmacist' \gset
SELECT min(staff_id) AS cashier_id FROM staff WHERE role = 'Cashier' \gset

INSERT INTO prescriptions (encounter_id, prescribed_by) VALUES (:enc_id, :doc_id)
RETURNING prescription_id AS rx_ok \gset
INSERT INTO prescription_items (prescription_id, drug_id, dose, frequency, duration_days, quantity) VALUES
  (:rx_ok, :para_id, '1 tab', 'Three times daily', 2, 6),
  (:rx_ok, :art_id,  '1 vial', 'Once daily',        2, 2);

INSERT INTO prescriptions (encounter_id, prescribed_by) VALUES (:enc_id, :doc_id)
RETURNING prescription_id AS rx_fail \gset
INSERT INTO prescription_items (prescription_id, drug_id, dose, frequency, duration_days, quantity) VALUES
  (:rx_fail, :para_id, '1 tab', 'Three times daily', 2, 6),
  (:rx_fail, :art_id,  '1 vial', 'Once daily',       20, 20);   -- more than will be in stock

SELECT p.patient_id AS adm_patient FROM patients p
WHERE NOT EXISTS (SELECT 1 FROM admissions a WHERE a.patient_id = p.patient_id AND a.discharged_at IS NULL)
ORDER BY p.patient_id LIMIT 1 \gset
SELECT admit_patient(:adm_patient, 'General', :doc_id, 'DEMO: malaria with dehydration') AS adm_id \gset

SELECT drug_id, generic_name, stock_quantity FROM drugs WHERE drug_id IN (:para_id, :art_id) ORDER BY drug_id;

\echo ''
\echo '================ T1  COMMIT: dispense prescription :rx_ok (6 x Paracetamol, 2 x Artesunate) ================'
BEGIN;
SELECT txid_current() AS t1_txid \gset
SELECT dispense_prescription(:rx_ok, :pharm_id) AS lines_dispensed;
\echo '--- inside the transaction, this session already sees the new stock:'
SELECT drug_id, generic_name, stock_quantity FROM drugs WHERE drug_id IN (:para_id, :art_id) ORDER BY drug_id;
COMMIT;
\echo '--- after COMMIT the change is durable and visible to everyone; the audit trail groups it by txid:'
SELECT prescription_id, status, dispensed_by FROM prescriptions WHERE prescription_id = :rx_ok;
SELECT table_name, operation, row_key, old_data, new_data, txid
FROM audit_log WHERE txid = :t1_txid ORDER BY audit_id;

\echo ''
\echo '================ T2  Automatic ROLLBACK: prescription :rx_fail needs 20 Artesunate, only 8 left ================'
\echo '--- Paracetamol (lower drug_id) is decremented first, then the Artesunate check fails:'
BEGIN;
SELECT dispense_prescription(:rx_fail, :pharm_id);
\echo '--- the transaction is now aborted; PostgreSQL refuses further work until it ends:'
SELECT 1;
ROLLBACK;
\echo '--- Paracetamol is still 94 (its decrement was undone) and the prescription is still Pending:'
SELECT drug_id, generic_name, stock_quantity FROM drugs WHERE drug_id IN (:para_id, :art_id) ORDER BY drug_id;
SELECT p.prescription_id, p.status, pi.drug_id, pi.quantity, pi.dispensed_quantity
FROM prescriptions p JOIN prescription_items pi USING (prescription_id)
WHERE p.prescription_id = :rx_fail ORDER BY pi.drug_id;

\echo ''
\echo '================ T3  Explicit ROLLBACK: a pharmacist mistypes a price change ================'
BEGIN;
UPDATE drugs SET unit_price = unit_price * 10 WHERE drug_id = :para_id;
SELECT generic_name, unit_price AS price_inside_tx FROM drugs WHERE drug_id = :para_id;
ROLLBACK;
SELECT generic_name, unit_price AS price_after_rollback FROM drugs WHERE drug_id = :para_id;

\echo ''
\echo '================ T4  SAVEPOINT: discharge, bill, then an insurance step that fails ================'
SELECT admission_id, bed_id, admitted_at, discharged_at FROM admissions WHERE admission_id = :adm_id;
BEGIN;
UPDATE admissions SET discharged_at = now() + interval '2 days',
                      discharge_summary = 'DEMO: recovered, discharged home'
WHERE admission_id = :adm_id;
UPDATE beds SET status = 'Available'
WHERE bed_id = (SELECT bed_id FROM admissions WHERE admission_id = :adm_id);
INSERT INTO invoices (invoice_no, patient_id, admission_id)
VALUES ('INV-DEMO-P3-01', :adm_patient, :adm_id) RETURNING invoice_id AS inv_id \gset
INSERT INTO invoice_items (invoice_id, item_type, description, quantity, unit_price)
VALUES (:inv_id, 'BedCharge', 'Ward bed charge (per night)', 2, 15000);
SAVEPOINT before_insurance;
\echo '--- optional step: automatic insurance claim posts the wrong amount and fails:'
SELECT record_payment(:inv_id, 999999, 'Insurance', :cashier_id);
\echo '--- undo only the failed step, keep the discharge and the invoice:'
ROLLBACK TO SAVEPOINT before_insurance;
\echo '--- the patient pays part of the bill at the desk instead:'
SELECT record_payment(:inv_id, 15000, 'POS', :cashier_id) AS invoice_status;
COMMIT;
SELECT a.admission_id, a.discharged_at IS NOT NULL AS discharged, b.bed_number, b.status AS bed_status
FROM admissions a JOIN beds b USING (bed_id) WHERE a.admission_id = :adm_id;
SELECT invoice_no, total_amount, amount_paid, balance_due, status FROM v_invoice_balance WHERE invoice_id = :inv_id;
SELECT payment_id, amount, method FROM payments WHERE invoice_id = :inv_id;

\echo ''
\echo '================ T5  MVCC: every UPDATE writes a new row version ================'
SELECT xmin, xmax, ctid, stock_quantity FROM drugs WHERE drug_id = :para_id;
BEGIN;
SELECT txid_current() AS my_txid;
UPDATE drugs SET stock_quantity = stock_quantity - 1 WHERE drug_id = :para_id;
\echo '--- new version: xmin is now this transaction, ctid (physical location) changed:'
SELECT xmin, xmax, ctid, stock_quantity FROM drugs WHERE drug_id = :para_id;
ROLLBACK;
\echo '--- after ROLLBACK the old version is current again; the dead one waits for VACUUM:'
SELECT xmin, xmax, ctid, stock_quantity FROM drugs WHERE drug_id = :para_id;

\echo ''
\echo '================ T6  Statement atomicity: take 50 of each demo drug in ONE UPDATE ================'
\echo '--- Paracetamol has enough (94), Artesunate does not (8); the CHECK fails on the second row:'
UPDATE drugs SET stock_quantity = stock_quantity - 50 WHERE drug_id IN (:para_id, :art_id);
SELECT drug_id, generic_name, stock_quantity FROM drugs WHERE drug_id IN (:para_id, :art_id) ORDER BY drug_id;

\echo ''
\echo '================ CLEANUP: remove demo rows (the audit trail is kept) ================'
BEGIN;
DELETE FROM payments      WHERE invoice_id = :inv_id;
DELETE FROM invoice_items WHERE invoice_id = :inv_id;
DELETE FROM invoices      WHERE invoice_id = :inv_id;
DELETE FROM encounters    WHERE admission_id = :adm_id;
DELETE FROM admissions    WHERE admission_id = :adm_id;
DELETE FROM prescription_items WHERE prescription_id IN (:rx_ok, :rx_fail);
DELETE FROM prescriptions      WHERE prescription_id IN (:rx_ok, :rx_fail);
DELETE FROM drugs WHERE drug_id IN (:para_id, :art_id);
COMMIT;
\echo 'Done.'
