"""
Q9 experiment: is the planner's hash join really the best choice?

The candidate index idx_rx_dispensed_at is created inside a transaction
that is rolled back at the end, so the database is left unchanged.
Each strategy is timed by wall clock (median of 9 runs).

Usage:  python experiments/q9_join_strategy.py > results/q9_join_strategy_output.txt
"""

import os
import statistics
import time

import psycopg

DSN = os.environ.get(
    "HOSPITAL_DSN",
    "postgresql://hospital_admin:hospital_pass@localhost:5432/stgrace_hospital",
)
Q9 = """
SELECT dr.generic_name, SUM(pi.dispensed_quantity)
FROM hospital.prescriptions rx
JOIN hospital.prescription_items pi ON pi.prescription_id = rx.prescription_id
JOIN hospital.drugs dr ON dr.drug_id = pi.drug_id
WHERE rx.dispensed_at >= '2026-10-04' AND rx.dispensed_at < '2026-11-03'
GROUP BY dr.drug_id"""

STRATEGIES = [
    ("planner's choice (parallel hash join)", []),
    ("hash join, no parallel workers", ["SET LOCAL max_parallel_workers_per_gather = 0"]),
    ("nested loop forced", ["SET LOCAL enable_hashjoin = off", "SET LOCAL enable_mergejoin = off"]),
]

with psycopg.connect(DSN) as conn:
    cur = conn.cursor()
    cur.execute("SET jit = off")
    cur.execute("CREATE INDEX idx_rx_dispensed_at ON hospital.prescriptions (dispensed_at)")
    cur.execute("ANALYZE hospital.prescriptions")
    for label, settings in STRATEGIES:
        cur.execute("SAVEPOINT s")
        for s in settings:
            cur.execute(s)
        join = next(line for (line,) in cur.execute("EXPLAIN " + Q9) if "Join" in line or "Loop" in line)
        timings = []
        for _ in range(9):
            start = time.perf_counter()
            cur.execute(Q9).fetchall()
            timings.append((time.perf_counter() - start) * 1000)
        print(f"{label:<40} median {statistics.median(timings):7.1f} ms   min {min(timings):7.1f} ms"
              f"   top join: {join.strip().split('  (')[0]}")
        cur.execute("ROLLBACK TO SAVEPOINT s")
    conn.rollback()                                    # removes the temporary index
