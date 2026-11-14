-- Removes everything created by 02_optimisations.sql (back to the Portfolio 1 baseline,
-- which keeps primary keys, unique constraints and foreign-key indexes).
SET search_path TO hospital, public;

DROP INDEX IF EXISTS idx_appt_doctor_start;
DROP INDEX IF EXISTS idx_patients_name_search;
DROP INDEX IF EXISTS idx_encounters_patient_date;
DROP INDEX IF EXISTS idx_lab_orders_outstanding;
DROP INDEX IF EXISTS idx_invoices_open;
DROP INDEX IF EXISTS idx_invoice_items_inv_total;
DROP INDEX IF EXISTS idx_payments_inv_amount;
DROP MATERIALIZED VIEW IF EXISTS mv_monthly_department_revenue;
DROP INDEX IF EXISTS idx_encounters_date;
DROP INDEX IF EXISTS idx_diagnoses_code_enc;
DROP INDEX IF EXISTS idx_rx_dispensed_at;
DROP INDEX IF EXISTS idx_lab_results_critical;
DROP INDEX IF EXISTS idx_admissions_patient_time;

SET client_min_messages = error;   -- hide "permission denied" notes for system catalogs
ANALYZE;
RESET client_min_messages;
