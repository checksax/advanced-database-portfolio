# Portfolio 2 — Query Processing and Optimisation

**Case study:** St. Grace Specialist Hospital
**DBMS:** PostgreSQL 18.6, database built in Portfolio 1 (about 2.6 million rows, 344 MB)

## Contents

| File | Purpose |
|---|---|
| `sql/01_workload_queries.sql` | 10 real hospital queries (Q1–Q10), plus rewritten forms of Q5 and Q6 |
| `sql/02_optimisations.sql` | 12 candidate indexes and 1 materialized view, each tied to a query |
| `sql/03_reset_optimisations.sql` | Returns the database to the Portfolio 1 baseline |
| `sql/04_advanced_techniques.sql` | Partitioning and pruning, partition archiving, MV refresh cost, planner statistics |
| `sql/05_final_index_set.sql` | Drops the 3 candidates the evidence rejected |
| `benchmark.py` | Runs the whole before/after experiment and writes the results |
| `plans/before/Qn.txt`, `plans/after/Qn.txt` | Full `EXPLAIN (ANALYZE, BUFFERS, SETTINGS)` output for every query |
| `results/benchmark_summary.md` / `.csv` | Timings, pages read, storage and write cost |
| `results/advanced_techniques_output.txt` | Output of `04_advanced_techniques.sql` |
| `experiments/q9_join_strategy.py` | Hash join vs nested loop vs parallel for Q9 (rolled back, no lasting change) |

## Method

1. **Baseline:** the Portfolio 1 schema, which already has primary keys, unique constraints
   and foreign-key indexes. This is a realistic, not artificially weak, starting point.
2. **Measure:** each query is run once to warm the cache, then **7 times by wall clock**, and
   the median is kept. One extra `EXPLAIN (ANALYZE, BUFFERS)` run captures the plan and the
   number of 8 KB pages touched.
3. **Optimise:** apply `02_optimisations.sql`, refresh statistics (`ANALYZE`), and repeat step 2.
4. **Check correctness:** each query's result set is fingerprinted (MD5) in both phases.
   A faster query that returns a different answer is a failure.
5. **Count the cost:** index size on disk, and bulk-insert time before and after.

Two measurement decisions, both found during the work:

- **Wall clock, not EXPLAIN's "Execution Time".** EXPLAIN ANALYZE times every row in every
  node. On this Windows machine, that overhead nearly doubled the cost of big scans (Q9:
  226 ms and 403 ms for the *same* plan). Timing the plain query measures what a nurse or
  cashier actually waits for.
- **JIT off for both phases.** JIT compilation adds tens of milliseconds to large plans,
  which would make the "before" numbers look worse than they really are.

## Results

All 10 queries returned **identical results** before and after.

| Query | What it serves | Before | After | Speed-up | Pages before → after | Technique |
|---|---|---:|---:|---:|---:|---|
| Q1 | Doctor's appointment list | 153.5 ms | 0.78 ms | **196×** | 2,158 → 38 | Composite B-tree |
| Q2 | Patient name search | 59.9 ms | 0.52 ms | **116×** | 1,104 → 13 | Expression index + `text_pattern_ops` |
| Q3 | Patient clinical history | 13.9 ms | 17.6 ms | 0.8× | 2,753 → 2,755 | (already optimal) |
| Q4 | Lab work list | 204.4 ms | 10.2 ms | **20×** | 5,230 → 3,271 | Partial index |
| Q5 | Outstanding balances | 2,657 ms | 333.6 ms | **8×** | 23,945 → 65,643 | Query rewrite + covering indexes |
| Q6 | Monthly revenue report | 1,166.8 ms | 1.2 ms | **971×** | 6,615 → 2 | Materialized view |
| Q7 | Malaria surveillance | 329.9 ms | 64.3 ms | **5×** | 3,923 → 463 | Range index + index-only scan |
| Q8 | Critical lab alerts | 189.3 ms | 8.0 ms | **24×** | 2,445 → 893 | Partial descending index |
| Q9 | Pharmacy stock days | 249.3 ms | 311.0 ms | 0.8× | 3,760 → 3,915 | (rejected) |
| Q10 | 30-day readmissions | 108.5 ms | 108.1 ms | 1.0× | 1,204 → 1,166 | (rejected) |

Run-to-run variation on this laptop was about ±20%, so the 0.8× and 1.0× results mean
"no change", not "slower".

## Analysis, query by query

**Q1: composite index, equality column first.** Before, PostgreSQL started two parallel
workers and scanned all 201k appointments, discarding 100,659 rows per worker to find 10.
With `(doctor_id, scheduled_start)`, one index range scan finds exactly the 10 rows, already
in time order, so the sort disappears too. Column order matters: with `scheduled_start`
first, the index would have to read every doctor's appointments for the day.

**Q2: expression index.** `WHERE lower(last_name) = 'okafor'` can't use an index on
`last_name`, because the function changes the value. Indexing `lower(last_name)` fixes that.
The database uses a non-C collation, so `LIKE 'ng%'` also needs `text_pattern_ops`. The plan
shows PostgreSQL rewriting the prefix into a range: `~>=~ 'ng' AND ~<~ 'nh'`.

**Q3: no change needed.** The Portfolio 1 foreign-key index `idx_encounters_patient` already
gives a plan made entirely of index lookups. The planner never used the extra
`(patient_id, encounter_date DESC)` index, so it was **dropped**. Worth noting: the planner
estimated **5** encounters for this patient, but there are **293**. Patient SGH-0000001 is a
very frequent visitor, and the planner's statistics describe an average patient. The plan
stayed correct here, but this kind of skew is a common source of bad plans.

**Q4: partial index.** Only 459 of 214,900 lab orders (0.2%) are outstanding. A partial index
`WHERE status IN ('Ordered','Collected')` stores only those rows: **32 kB**, compared with
megabytes for a full index on `status`. It stays small as history grows, because completed
orders drop out of it automatically.

**Q5: the rewrite mattered more than the index.** The view `v_invoice_balance` aggregates all
667k invoice items and 228k payments, then filters. The rewrite filters first: only Unpaid or
Partial invoices can have a balance. It then sums per invoice with `LATERAL` subqueries,
which read only from the covering indexes (`INCLUDE (line_total)` and `INCLUDE (amount)`).
Result: 8× faster, even though it touches **more** pages (65k vs 24k). Those pages are
random index lookups that are already in memory, while the original did large sorts and
merges. **Pages touched and time don't always move together.** The rewrite depends on the
rule "a Paid invoice has no balance", which Portfolio 1's consistency check verified.

**Q6: materialized view.** The report joins five tables and aggregates 114k payments to
produce 72 rows. History doesn't change, so it is computed once and stored. Reading 2 pages
takes 1.2 ms. **The trade-off:** the data is only as fresh as the last refresh. A refresh
costs about 3.8 s, or 4.3 s with `CONCURRENTLY`, which lets reports keep running during
the refresh. A nightly refresh suits a management report; it would not suit a live bed board.

**Q7: range index plus index-only scan.** `idx_encounters_date` narrows encounters to the
13-week window (21,712 rows). `idx_diagnoses_code_enc (icd10_code, encounter_id)` lets the
malaria filter run without reading the diagnoses table at all (Index Only Scan). Pages
dropped from 3,923 to 463.

**Q8: partial descending index.** Critical results are about 1% of 210k lab results. The
index `(resulted_at DESC) WHERE flag = 'Critical'` returns the 76 newest critical results in
order, with no sort. The index is 224 kB.

**Q9: an index that didn't help.** `idx_rx_dispensed_at` did make its own step faster: it
found 5,548 prescriptions in about 4 ms instead of a full scan. But the query's cost is
joining those to `prescription_items`, and the planner still scans and hashes all 282k
items. **Dropped.** `experiments/q9_join_strategy.py` tested the alternatives (wall-clock
median of 9 runs, `results/q9_join_strategy_output.txt`):

| Strategy | Median |
|---|---:|
| Planner's choice: parallel hash join | 314 ms |
| Hash join, parallel workers disabled | **136 ms** |
| Nested loop (forced) | 156 ms |

The planner picked the right join type, a hash join, but the wrong degree of parallelism.
Starting worker processes on Windows costs more than the work they share. A sensible
follow-up for this server would be to raise `parallel_setup_cost` (default 1000), so that
mid-sized queries stay single-process. I didn't change it here, because it is a server-wide
setting and would affect every query in the benchmark.

**Q10: removed a sort but saved no time.** `idx_admissions_patient_time` matches the window
function's `PARTITION BY patient_id ORDER BY admitted_at`, so the plan became an Index Only
Scan with no sort. But admissions is small (9,737 rows), and the query's time goes into
hashing all 50,000 patients to label 108 results. **Dropped.** The real cause is a 15×
cardinality mis-estimate: the planner expected 1,622 rows from the `gap <= 30 days` filter
because it can't estimate a filter on a window-function result, and so it chose a hash join.

## Advanced techniques (`04_advanced_techniques.sql`)

**Range partitioning.** A copy of `appointments` was partitioned by quarter (9 partitions,
about 23k rows each).

| Monthly no-show report (Oct 2026) | Plan | Time |
|---|---|---:|
| Unpartitioned table | Bitmap scan using `idx_appt_doctor_start` via **skip scan** (65 index searches, one per doctor) | 56.1 ms |
| Partitioned table | **Partition pruning**: only `appointments_2026_q4` is scanned | 22.3 ms |
| Partitioned, prepared statement | **Run-time pruning**: `Subplans Removed: 8` | 25.3 ms |

PostgreSQL 18's new B-tree **skip scan** used the `(doctor_id, scheduled_start)` index even
though the query doesn't filter on `doctor_id`. Older versions would have scanned the whole
table. Partitioning still won, because pruning removed 8 of 9 partitions before execution.

**Archiving old data.** Removing Q4 2024 (14,944 rows) with `DELETE` took 58 ms and leaves
dead rows for VACUUM to clean up. `ALTER TABLE … DETACH PARTITION` took 8 ms and only
changes metadata. The rows become a standalone archive table. The gap widens as tables grow.

**What the planner knows (`pg_stats`).**

| Column | n_distinct | Correlation | Meaning |
|---|---:|---:|---|
| encounters.encounter_date | −0.64 (64% unique) | **1.000** | Rows are stored in date order, so date-range index scans read neighbouring pages. That makes Q7's index cheap. |
| appointments.doctor_id | 64 | 0.023 | Doctors are spread randomly, which is why Q1 needs `doctor_id` as the leading index column. |
| lab_orders.status | 4 | 0.956 | Highly skewed: almost every order is Completed. That makes a partial index ideal. |

## Cost of the optimisations

- **Storage:** final set **≈ 43 MB** (12% of the 344 MB database). The largest is the
  covering index on `invoice_items` (20 MB).
- **Writes:** bulk-inserting 20,000 encounters went from 2.8 s to 4.8 s (**+70%**) with two
  extra indexes on that table. The `invoice_items` change (−7%) is within noise. Encounters
  are written one at a time at the front desk, so this cost is acceptable, but it is why
  unhelpful indexes were removed rather than kept "just in case".
- **Observed side-effect:** inserts that were rolled back during testing still left dead
  index entries. `idx_invoice_items_inv_total` grew from 20 MB to 26 MB until `VACUUM` ran.

## Decisions

| Kept (10 objects) | Dropped (3) |
|---|---|
| `idx_appt_doctor_start`, `idx_patients_name_search`, `idx_lab_orders_outstanding`, `idx_invoices_open`, `idx_invoice_items_inv_total`, `idx_payments_inv_amount`, `idx_encounters_date`, `idx_diagnoses_code_enc`, `idx_lab_results_critical`, `mv_monthly_department_revenue` (+ its unique index) | `idx_encounters_patient_date` (never used), `idx_rx_dispensed_at` (no end-to-end gain), `idx_admissions_patient_time` (no end-to-end gain) |

## Limitations

- Warm-cache timings on a single laptop. A production server with cold caches and
  concurrent users would show bigger absolute numbers. The relative ranking should hold.
- Parallel-worker startup is expensive on Windows (about 90 ms before the first row in
  several baseline plans), which inflates the "before" times of parallel plans.
- Synthetic data: the distributions are realistic but not real. Q3 showed how real-world
  skew (frequent visitors) breaks planner assumptions.

## How to run

```powershell
python benchmark.py                                   # about 2 minutes; rewrites plans/ and results/
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h localhost -U hospital_admin -d stgrace_hospital -f sql\04_advanced_techniques.sql
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h localhost -U hospital_admin -d stgrace_hospital -f sql\05_final_index_set.sql
```
