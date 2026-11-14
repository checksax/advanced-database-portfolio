-- =====================================================================
-- St. Grace Specialist Hospital - Portfolio 2 optimisations
-- Each object targets one query in 01_workload_queries.sql.
-- Undo with 03_reset_optimisations.sql.
-- =====================================================================
SET search_path TO hospital, public;

-- Q1: composite B-tree. Equality column first (doctor_id), range column second
-- (scheduled_start), so one index range scan returns the rows already sorted.
CREATE INDEX idx_appt_doctor_start
    ON appointments (doctor_id, scheduled_start);

-- Q2: expression index. A plain index on last_name cannot serve lower(last_name).
-- text_pattern_ops lets LIKE 'ng%' use the index under a non-C collation.
CREATE INDEX idx_patients_name_search
    ON patients (lower(last_name) text_pattern_ops, lower(first_name) text_pattern_ops);

-- Q3: covering index. Adds encounter_date so the newest-first ordering is read
-- straight from the index for that patient.
CREATE INDEX idx_encounters_patient_date
    ON encounters (patient_id, encounter_date DESC);

-- Q4: partial index. Only about 0.2% of lab orders are outstanding, so the index
-- stores just those rows and stays tiny however large history grows.
CREATE INDEX idx_lab_orders_outstanding
    ON lab_orders (ordered_at)
    WHERE status IN ('Ordered', 'Collected');

-- Q5: partial index on unpaid invoices + covering index on items so SUM(line_total)
-- per invoice is an index-only scan.
CREATE INDEX idx_invoices_open
    ON invoices (invoice_id)
    WHERE status IN ('Unpaid', 'Partial');
CREATE INDEX idx_invoice_items_inv_total
    ON invoice_items (invoice_id) INCLUDE (line_total);
CREATE INDEX idx_payments_inv_amount
    ON payments (invoice_id) INCLUDE (amount);

-- Q6: materialized view. The monthly report is read many times but the past
-- never changes, so the expensive join+aggregate is computed once.
-- Trade-off: data is as fresh as the last REFRESH (nightly job).
CREATE MATERIALIZED VIEW mv_monthly_department_revenue AS
SELECT date_trunc('month', py.paid_at) AS month,
       dpt.name                         AS department,
       SUM(py.amount)                   AS revenue,
       COUNT(*)                         AS payments
FROM payments    py
JOIN invoices    i   ON i.invoice_id = py.invoice_id
JOIN encounters  e   ON e.encounter_id = i.encounter_id
JOIN staff       s   ON s.staff_id = e.doctor_id
JOIN departments dpt ON dpt.department_id = s.department_id
GROUP BY 1, 2;
-- unique index makes REFRESH ... CONCURRENTLY possible (no read lock during refresh)
CREATE UNIQUE INDEX uq_mv_revenue ON mv_monthly_department_revenue (month, department);

-- Q7 and Q9: B-tree indexes on the date columns used for range filters.
CREATE INDEX idx_encounters_date      ON encounters (encounter_date);
CREATE INDEX idx_diagnoses_code_enc   ON diagnoses (icd10_code, encounter_id);
CREATE INDEX idx_rx_dispensed_at      ON prescriptions (dispensed_at);

-- Q8: partial + descending index for the critical-results alert list.
CREATE INDEX idx_lab_results_critical
    ON lab_results (resulted_at DESC)
    WHERE flag = 'Critical';

-- Q10: index matches the window's PARTITION BY / ORDER BY so no sort is needed,
-- and INCLUDE makes it covering.
CREATE INDEX idx_admissions_patient_time
    ON admissions (patient_id, admitted_at) INCLUDE (discharged_at, admission_id);

SET client_min_messages = error;   -- hide "permission denied" notes for system catalogs
ANALYZE;
RESET client_min_messages;
