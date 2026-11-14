-- =====================================================================
-- St. Grace Specialist Hospital - Data profile after loading
-- Evidence that the database is populated and internally consistent.
--   psql -U hospital_admin -d stgrace_hospital -f 04_data_profile.sql > ../evidence/data_profile_output.txt
-- =====================================================================
SET search_path TO hospital, public;

\echo '=== Row counts per table ==='
SELECT relname AS table_name, n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE schemaname = 'hospital'
ORDER BY n_live_tup DESC;

\echo '=== Constraints defined, by type ==='
SELECT CASE contype WHEN 'p' THEN 'PRIMARY KEY' WHEN 'f' THEN 'FOREIGN KEY' WHEN 'u' THEN 'UNIQUE'
                    WHEN 'c' THEN 'CHECK' WHEN 'x' THEN 'EXCLUDE'
                    WHEN 'n' THEN 'NOT NULL' END AS constraint_type,   -- 'n' is catalogued from PostgreSQL 18
       COUNT(*) AS how_many
FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
WHERE n.nspname = 'hospital'
GROUP BY contype ORDER BY how_many DESC;

\echo '=== Consistency checks (every count should be 0) ==='
-- Overlaps are found by comparing each row with the previous one in time
-- order (window function), which is O(n log n) instead of a self-join.
SELECT 'overlapping doctor appointments' AS check_name, COUNT(*) AS violations
FROM (SELECT scheduled_start,
             lag(scheduled_end) OVER (PARTITION BY doctor_id ORDER BY scheduled_start) AS prev_end
      FROM appointments WHERE status <> 'Cancelled') s
WHERE prev_end > scheduled_start
UNION ALL
SELECT 'overlapping stays in the same bed', COUNT(*)
FROM (SELECT admitted_at,
             lag(COALESCE(discharged_at, 'infinity')) OVER (PARTITION BY bed_id ORDER BY admitted_at) AS prev_end
      FROM admissions) s
WHERE prev_end > admitted_at
UNION ALL
SELECT 'occupied flag disagrees with active admissions', COUNT(*)
FROM beds b
WHERE (b.status = 'Occupied') <> EXISTS (SELECT 1 FROM admissions a
                                         WHERE a.bed_id = b.bed_id AND a.discharged_at IS NULL)
UNION ALL
SELECT 'invoices overpaid', COUNT(*) FROM v_invoice_balance WHERE balance_due < 0
UNION ALL
SELECT 'Paid invoices with balance left', COUNT(*) FROM v_invoice_balance WHERE status = 'Paid' AND balance_due > 0.01
UNION ALL
SELECT 'maternity diagnoses on male patients', COUNT(*)
FROM diagnoses d JOIN encounters e USING (encounter_id) JOIN patients p USING (patient_id)
WHERE d.icd10_code LIKE 'O%' AND p.gender = 'M';

\echo '=== Sample: one patient''s summary ==='
SELECT * FROM v_patient_summary WHERE patient_id = 1;

\echo '=== Sample: current bed occupancy ==='
SELECT * FROM v_bed_occupancy ORDER BY ward_id;
