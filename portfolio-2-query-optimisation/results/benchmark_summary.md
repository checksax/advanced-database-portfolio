# Benchmark summary

Run: 2026-11-03 21:46, PostgreSQL 18.6, wall-clock median of 7 warm runs, JIT off.

| Query | Purpose | Rows | Same result | Before (ms) | After (ms) | Speed-up | Pages before | Pages after |
|---|---|---:|:---:|---:|---:|---:|---:|---:|
| Q1 | Reception: a doctor's appointment list for tomorrow | 10 | yes | 153.53 | 0.78 | 196.2x | 2,158 | 38 |
| Q2 | Records: find a patient by surname and first-name prefix (case-insensitive) | 11 | yes | 59.94 | 0.52 | 116.0x | 1,104 | 13 |
| Q3 | Clinic: full clinical history of one patient (newest first) | 356 | yes | 13.94 | 17.60 | 0.8x | 2,753 | 2,755 |
| Q4 | Laboratory: outstanding work list (not yet resulted), STAT first | 459 | yes | 204.43 | 10.23 | 20.0x | 5,230 | 3,271 |
| Q5 (rewritten) | Finance: top 50 outstanding invoice balances (through the view) | 50 | yes | 2657.43 | 333.63 | 8.0x | 23,945 | 65,643 |
| Q6 (rewritten) | Management: monthly revenue per department, last 12 months | 72 | yes | 1166.80 | 1.20 | 971.4x | 6,615 | 2 |
| Q7 | Public health: weekly malaria diagnoses, last 13 weeks | 14 | yes | 329.94 | 64.26 | 5.1x | 3,923 | 463 |
| Q8 | Clinical safety: critical lab results in the last 7 days, with patient phone | 76 | yes | 189.27 | 7.97 | 23.7x | 2,445 | 893 |
| Q9 | Pharmacy: 30-day consumption per drug and days of stock left | 20 | yes | 249.29 | 311.03 | 0.8x | 3,760 | 3,915 |
| Q10 | Quality: readmissions within 30 days of discharge, last 12 months | 108 | yes | 108.52 | 108.07 | 1.0x | 1,204 | 1,166 |

Pages = shared buffer pages (8 KB) touched by the whole plan.

## Storage cost of the optimisations

| Object | On table | Size |
|---|---|---:|
| `idx_invoice_items_inv_total` | invoice_items | 20 MB |
| `idx_payments_inv_amount` | payments | 7032 kB |
| `idx_appt_doctor_start` | appointments | 6224 kB |
| `idx_diagnoses_code_enc` | diagnoses | 5952 kB |
| `idx_encounters_patient_date` | encounters | 5344 kB |
| `idx_encounters_date` | encounters | 3536 kB |
| `idx_rx_dispensed_at` | prescriptions | 2912 kB |
| `idx_patients_name_search` | patients | 520 kB |
| `idx_admissions_patient_time` | admissions | 488 kB |
| `idx_invoices_open` | invoices | 256 kB |
| `idx_lab_results_critical` | lab_results | 224 kB |
| `idx_lab_orders_outstanding` | lab_orders | 32 kB |
| `uq_mv_revenue` | mv_monthly_department_revenue | 16 kB |
| `mv_monthly_department_revenue` | (materialized view) | 16 kB |
| **Total** | | **51.9 MB** |

## Write cost of the optimisations

Bulk INSERT inside a rolled-back transaction, median of 3.

| Insert | Before (ms) | After (ms) | Change |
|---|---:|---:|---:|
| invoice_items +50,000 rows | 4859.4 | 4499.0 | -7% |
| encounters +20,000 rows | 2818.8 | 4787.7 | +70% |
