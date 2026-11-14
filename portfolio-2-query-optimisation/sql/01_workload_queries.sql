-- =====================================================================
-- St. Grace Specialist Hospital - Portfolio 2 workload
-- Ten queries the hospital runs every day, from reception to finance.
--
-- Format used by ../benchmark.py:
--   "-- @@ Qn | title"        query run before AND after optimisation
--   "-- @@ Qn-after | title"  rewritten form used only after optimisation
-- Every query ends with a semicolon on its own line.
--
-- "Today" in the simulated hospital is 2026-11-03, so date ranges are
-- fixed literals; this keeps results identical on every run.
-- =====================================================================

-- @@ Q1 | Reception: a doctor's appointment list for tomorrow
SELECT a.scheduled_start,
       a.status,
       p.hospital_no,
       p.first_name || ' ' || p.last_name AS patient
FROM hospital.appointments a
JOIN hospital.patients p ON p.patient_id = a.patient_id
WHERE a.doctor_id = 23
  AND a.scheduled_start >= '2026-11-04'
  AND a.scheduled_start <  '2026-11-05'
ORDER BY a.scheduled_start
;

-- @@ Q2 | Records: find a patient by surname and first-name prefix (case-insensitive)
SELECT patient_id, hospital_no, first_name, last_name, date_of_birth, phone
FROM hospital.patients
WHERE lower(last_name) = 'okafor'
  AND lower(first_name) LIKE 'ng%'
ORDER BY date_of_birth
;

-- @@ Q3 | Clinic: full clinical history of one patient (newest first)
SELECT e.encounter_date,
       e.encounter_type,
       s.last_name  AS doctor,
       d.icd10_code,
       c.description AS diagnosis,
       d.diagnosis_type
FROM hospital.patients p
JOIN hospital.encounters  e ON e.patient_id = p.patient_id
JOIN hospital.staff       s ON s.staff_id = e.doctor_id
LEFT JOIN hospital.diagnoses   d ON d.encounter_id = e.encounter_id
LEFT JOIN hospital.icd10_codes c ON c.code = d.icd10_code
WHERE p.hospital_no = 'SGH-0000001'
ORDER BY e.encounter_date DESC
;

-- @@ Q4 | Laboratory: outstanding work list (not yet resulted), STAT first
SELECT lo.lab_order_id,
       lo.priority,
       lo.ordered_at,
       t.test_code,
       p.hospital_no,
       lo.status
FROM hospital.lab_orders lo
JOIN hospital.lab_tests  t ON t.test_id = lo.test_id
JOIN hospital.encounters e ON e.encounter_id = lo.encounter_id
JOIN hospital.patients   p ON p.patient_id = e.patient_id
WHERE lo.status IN ('Ordered', 'Collected')
ORDER BY CASE lo.priority WHEN 'STAT' THEN 1 WHEN 'Urgent' THEN 2 ELSE 3 END,
         lo.ordered_at
;

-- @@ Q5 | Finance: top 50 outstanding invoice balances (through the view)
SELECT invoice_no, patient_id, total_amount, amount_paid, balance_due
FROM hospital.v_invoice_balance
WHERE balance_due > 0
ORDER BY balance_due DESC
LIMIT 50
;

-- @@ Q5-after | Finance: same result, rewritten to aggregate only unpaid invoices
-- Only 'Unpaid'/'Partial' invoices can have a balance (verified in Portfolio 1:
-- 0 'Paid' invoices with balance left), so filter first and aggregate per
-- invoice with LATERAL subqueries instead of aggregating every row in the view.
SELECT i.invoice_no,
       i.patient_id,
       it.total                         AS total_amount,
       COALESCE(pm.paid, 0)             AS amount_paid,
       it.total - COALESCE(pm.paid, 0)  AS balance_due
FROM hospital.invoices i
CROSS JOIN LATERAL (SELECT SUM(line_total) AS total
                    FROM hospital.invoice_items ii WHERE ii.invoice_id = i.invoice_id) it
CROSS JOIN LATERAL (SELECT SUM(amount) AS paid
                    FROM hospital.payments py WHERE py.invoice_id = i.invoice_id) pm
WHERE i.status IN ('Unpaid', 'Partial')
  AND it.total - COALESCE(pm.paid, 0) > 0
ORDER BY balance_due DESC
LIMIT 50
;

-- @@ Q6 | Management: monthly revenue per department, last 12 months
SELECT date_trunc('month', py.paid_at) AS month,
       dpt.name                         AS department,
       SUM(py.amount)                   AS revenue
FROM hospital.payments    py
JOIN hospital.invoices    i   ON i.invoice_id = py.invoice_id
JOIN hospital.encounters  e   ON e.encounter_id = i.encounter_id
JOIN hospital.staff       s   ON s.staff_id = e.doctor_id
JOIN hospital.departments dpt ON dpt.department_id = s.department_id
WHERE py.paid_at >= '2025-11-01' AND py.paid_at < '2026-11-01'
GROUP BY 1, 2
ORDER BY 1, 2
;

-- @@ Q6-after | Management: same report read from a materialized view
SELECT month, department, revenue
FROM hospital.mv_monthly_department_revenue
WHERE month >= '2025-11-01' AND month < '2026-11-01'
ORDER BY 1, 2
;

-- @@ Q7 | Public health: weekly malaria diagnoses, last 13 weeks
SELECT date_trunc('week', e.encounter_date) AS week,
       COUNT(*)                             AS malaria_cases
FROM hospital.diagnoses  d
JOIN hospital.encounters e ON e.encounter_id = d.encounter_id
WHERE d.icd10_code IN ('B54', 'B50.9')
  AND e.encounter_date >= '2026-08-04'
  AND e.encounter_date <  '2026-11-03'
GROUP BY 1
ORDER BY 1
;

-- @@ Q8 | Clinical safety: critical lab results in the last 7 days, with patient phone
SELECT r.resulted_at,
       t.name AS test,
       COALESCE(r.result_value::text, r.result_text) AS result,
       p.hospital_no,
       p.phone
FROM hospital.lab_results r
JOIN hospital.lab_orders  lo ON lo.lab_order_id = r.lab_order_id
JOIN hospital.lab_tests   t  ON t.test_id = lo.test_id
JOIN hospital.encounters  e  ON e.encounter_id = lo.encounter_id
JOIN hospital.patients    p  ON p.patient_id = e.patient_id
WHERE r.flag = 'Critical'
  AND r.resulted_at >= '2026-10-27'
ORDER BY r.resulted_at DESC
;

-- @@ Q9 | Pharmacy: 30-day consumption per drug and days of stock left
SELECT dr.generic_name,
       dr.strength,
       dr.stock_quantity,
       SUM(pi.dispensed_quantity)                                         AS used_30d,
       ROUND(dr.stock_quantity / NULLIF(SUM(pi.dispensed_quantity) / 30.0, 0)) AS days_of_stock
FROM hospital.prescriptions      rx
JOIN hospital.prescription_items pi ON pi.prescription_id = rx.prescription_id
JOIN hospital.drugs              dr ON dr.drug_id = pi.drug_id
WHERE rx.dispensed_at >= '2026-10-04' AND rx.dispensed_at < '2026-11-03'
GROUP BY dr.drug_id, dr.generic_name, dr.strength, dr.stock_quantity
ORDER BY days_of_stock NULLS LAST
;

-- @@ Q10 | Quality: readmissions within 30 days of discharge, last 12 months
WITH stays AS (
    SELECT patient_id,
           admission_id,
           admitted_at,
           LAG(discharged_at) OVER (PARTITION BY patient_id ORDER BY admitted_at) AS previous_discharge
    FROM hospital.admissions
)
SELECT p.hospital_no,
       s.admission_id,
       s.previous_discharge,
       s.admitted_at,
       s.admitted_at - s.previous_discharge AS gap
FROM stays s
JOIN hospital.patients p ON p.patient_id = s.patient_id
WHERE s.previous_discharge IS NOT NULL
  AND s.admitted_at - s.previous_discharge <= INTERVAL '30 days'
  AND s.admitted_at >= '2025-11-03'
ORDER BY gap
;
