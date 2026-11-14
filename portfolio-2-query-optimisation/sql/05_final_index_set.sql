-- =====================================================================
-- St. Grace Specialist Hospital - final index decision
-- Run after 02_optimisations.sql. Removes the candidate indexes that the
-- benchmark showed are not worth their write and storage cost
-- (see ../README.md, "Decisions").
-- =====================================================================
SET search_path TO hospital, public;

-- Q3: never chosen by the planner; idx_encounters_patient already serves the query.
DROP INDEX IF EXISTS idx_encounters_patient_date;

-- Q9: speeds up the prescriptions step, but total time is dominated by the
-- prescription_items scan, so there was no end-to-end gain (0.8x).
DROP INDEX IF EXISTS idx_rx_dispensed_at;

-- Q10: removes a 9,737-row sort, but the admissions table is small and the
-- query cost is dominated by hashing patients, so there was no gain (1.0x).
DROP INDEX IF EXISTS idx_admissions_patient_time;

SET client_min_messages = error;   -- hide "permission denied" notes for system catalogs
ANALYZE;
RESET client_min_messages;

\echo 'Indexes added by Portfolio 2 that remain:'
SELECT c.relname             AS index_name,
       i.indrelid::regclass  AS on_table,
       pg_size_pretty(pg_relation_size(c.oid)) AS size
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'hospital'
  AND c.relname IN ('idx_appt_doctor_start', 'idx_patients_name_search',
      'idx_lab_orders_outstanding', 'idx_invoices_open', 'idx_invoice_items_inv_total',
      'idx_payments_inv_amount', 'idx_encounters_date', 'idx_diagnoses_code_enc',
      'idx_lab_results_critical', 'uq_mv_revenue')
ORDER BY pg_relation_size(c.oid) DESC;
