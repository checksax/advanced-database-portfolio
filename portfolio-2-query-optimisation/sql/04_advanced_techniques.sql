-- =====================================================================
-- St. Grace Specialist Hospital - Portfolio 2 advanced techniques
--   A. Range partitioning of appointments + partition pruning
--   B. Partition maintenance: archiving an old year by DETACH
--   C. Materialized view refresh cost (plain vs CONCURRENTLY)
--   D. What the planner knows: pg_stats and an estimation error
--
--   psql -U hospital_admin -d stgrace_hospital -f 04_advanced_techniques.sql
--        > ../results/advanced_techniques_output.txt 2>&1
-- Requires 02_optimisations.sql to have been applied (for section C).
-- =====================================================================
SET search_path TO hospital, public;
SET jit = off;
\timing on

-- ---------------------------------------------------------------------
\echo '=== A1. Build a range-partitioned copy of appointments (one partition per quarter) ==='
DROP TABLE IF EXISTS appointments_part CASCADE;
DROP TABLE IF EXISTS appointments_2024_q4_archive;

CREATE TABLE appointments_part (
    appointment_id   INT          NOT NULL,
    patient_id       INT          NOT NULL,
    doctor_id        INT          NOT NULL,
    scheduled_start  TIMESTAMP    NOT NULL,
    scheduled_end    TIMESTAMP    NOT NULL,
    status           VARCHAR(12)  NOT NULL,
    reason           VARCHAR(200),
    booked_at        TIMESTAMP    NOT NULL,
    -- the partition key must be part of any primary key on a partitioned table
    PRIMARY KEY (appointment_id, scheduled_start)
) PARTITION BY RANGE (scheduled_start);

DO $$
DECLARE q_start date := '2024-10-01';
BEGIN
    WHILE q_start < '2027-01-01' LOOP
        EXECUTE format('CREATE TABLE appointments_%s_q%s PARTITION OF appointments_part
                        FOR VALUES FROM (%L) TO (%L)',
                       extract(year from q_start), extract(quarter from q_start),
                       q_start, q_start + interval '3 months');
        q_start := q_start + interval '3 months';
    END LOOP;
END $$;

INSERT INTO appointments_part SELECT * FROM appointments;
CREATE INDEX ON appointments_part (doctor_id, scheduled_start);   -- cascades to every partition
ANALYZE appointments_part;

\echo '--- rows per partition'
SELECT tableoid::regclass AS partition, COUNT(*) AS rows
FROM appointments_part GROUP BY 1 ORDER BY 1;

\echo '=== A2. Monthly no-show report: unpartitioned table (PG18 skip scan on idx_appt_doctor_start, one search per doctor) ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT status, COUNT(*) FROM appointments
WHERE scheduled_start >= '2026-10-01' AND scheduled_start < '2026-11-01'
GROUP BY status;

\echo '=== A3. Same report on the partitioned table: partition pruning reads ONE quarter ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT status, COUNT(*) FROM appointments_part
WHERE scheduled_start >= '2026-10-01' AND scheduled_start < '2026-11-01'
GROUP BY status;

\echo '=== A4. Pruning also works at run time (value only known when the query executes) ==='
PREPARE month_report(timestamp) AS
SELECT status, COUNT(*) FROM appointments_part
WHERE scheduled_start >= $1 AND scheduled_start < $1 + interval '1 month'
GROUP BY status;
SET plan_cache_mode = force_generic_plan;
EXPLAIN (ANALYZE, COSTS OFF) EXECUTE month_report('2026-10-01');
RESET plan_cache_mode;
DEALLOCATE month_report;

-- ---------------------------------------------------------------------
\echo '=== B. Archiving: detach the oldest quarter (metadata-only, no row copying) ==='
\echo '--- for comparison, removing the same rows with DELETE (row by row, then rolled back):'
BEGIN;
EXPLAIN (ANALYZE, COSTS OFF)
DELETE FROM appointments_part WHERE scheduled_start < '2025-01-01';
ROLLBACK;
\echo '--- detach (instant):'
ALTER TABLE appointments_part DETACH PARTITION appointments_2024_q4;
ALTER TABLE appointments_2024_q4 RENAME TO appointments_2024_q4_archive;
SELECT COUNT(*) AS rows_now_in_archive_table FROM appointments_2024_q4_archive;

-- ---------------------------------------------------------------------
\echo '=== C. Materialized view maintenance cost ==='
\echo '--- plain refresh (blocks readers while it runs):'
REFRESH MATERIALIZED VIEW mv_monthly_department_revenue;
\echo '--- concurrent refresh (readers keep working; needs the unique index; slower):'
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_department_revenue;

-- ---------------------------------------------------------------------
\echo '=== D1. Planner statistics for columns used in the workload ==='
SELECT tablename, attname, n_distinct, round(correlation::numeric, 3) AS correlation,
       most_common_vals::text AS most_common_values
FROM pg_stats
WHERE schemaname = 'hospital'
  AND (tablename, attname) IN (('encounters', 'encounter_date'), ('lab_orders', 'status'),
                               ('appointments', 'doctor_id'), ('lab_results', 'flag'),
                               ('invoices', 'status'))
ORDER BY tablename, attname;

\echo '=== D2. Estimation error: planner guesses 1/3 of rows pass a filter on a computed column ==='
EXPLAIN (ANALYZE, COSTS ON, TIMING OFF)
WITH stays AS (
    SELECT patient_id, admitted_at,
           LAG(discharged_at) OVER (PARTITION BY patient_id ORDER BY admitted_at) AS previous_discharge
    FROM admissions)
SELECT COUNT(*) FROM stays
WHERE admitted_at - previous_discharge <= INTERVAL '30 days';

-- tidy up the demo objects
DROP TABLE appointments_part CASCADE;
DROP TABLE appointments_2024_q4_archive;
\echo '=== Done ==='
