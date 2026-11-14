"""
Portfolio 2 benchmark: before/after optimisation of the hospital workload.

Steps
  1. Reset to the Portfolio 1 baseline (03_reset_optimisations.sql).
  2. For each query: 1 warm-up run, then RUNS timed executions (wall clock,
     median kept), then one EXPLAIN (ANALYZE, BUFFERS) whose plan is saved.
  3. Apply 02_optimisations.sql.
  4. Repeat step 2, using the "-after" rewrite where one exists.
  5. Write results/benchmark_results.csv and results/benchmark_summary.md.

Why wall clock and not EXPLAIN's "Execution Time": EXPLAIN ANALYZE times
every row of every node, and on Windows that instrumentation overhead
roughly doubled the cost of large scans in our tests. Timing the plain
query measures what a user actually waits for.

Timings are warm-cache medians, which is how a busy hospital server behaves.
JIT is disabled for both phases: its compile time (tens of ms) would
otherwise dominate the large "before" plans and exaggerate the gains.

Usage:  python benchmark.py
"""

import csv
import hashlib
import os
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

import psycopg

DSN = os.environ.get(
    "HOSPITAL_DSN",
    "postgresql://hospital_admin:hospital_pass@localhost:5432/stgrace_hospital",
)
RUNS = 7
HERE = Path(__file__).resolve().parent
SQL = HERE / "sql"
PLANS = HERE / "plans"
RESULTS = HERE / "results"

MARKER = re.compile(r"^-- @@ (\S+) \| (.+)$", re.M)


def load_queries() -> dict[str, tuple[str, str]]:
    """Return {"Q1": (title, sql), "Q5-after": (title, sql), ...}."""
    text = (SQL / "01_workload_queries.sql").read_text(encoding="utf-8")
    parts = MARKER.split(text)[1:]               # [id, title, body, id, title, body, ...]
    queries = {}
    for qid, title, body in zip(parts[0::3], parts[1::3], parts[2::3]):
        sql = body.strip().rstrip(";").strip()
        queries[qid] = (title.strip(), sql)
    return queries


def explain(cur, sql: str) -> tuple[float, float, int, str]:
    rows = cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, SETTINGS) {sql}").fetchall()
    plan = "\n".join(r[0] for r in rows)
    exec_ms = float(re.search(r"Execution Time: ([\d.]+) ms", plan).group(1))
    plan_ms = float(re.search(r"Planning Time: ([\d.]+) ms", plan).group(1))
    m = re.search(r"Buffers: shared( hit=(\d+))?( read=(\d+))?", plan)   # first = root node
    pages = int(m.group(2) or 0) + int(m.group(4) or 0) if m else 0
    return exec_ms, plan_ms, pages, plan


def run_phase(cur, phase: str, queries: dict) -> dict[str, dict]:
    out = {}
    (PLANS / phase).mkdir(parents=True, exist_ok=True)
    for qid in sorted((q for q in queries if "-" not in q), key=lambda q: int(q[1:])):
        variant = f"{qid}-after" if phase == "after" and f"{qid}-after" in queries else qid
        title, sql = queries[variant]
        result = cur.execute(sql).fetchall()                    # warm-up, row count, fingerprint
        rows = len(result)
        fingerprint = hashlib.md5(repr(sorted(map(repr, result))).encode()).hexdigest()
        timings = []
        for _ in range(RUNS):
            start = time.perf_counter()
            cur.execute(sql).fetchall()
            timings.append((time.perf_counter() - start) * 1000)
        median = statistics.median(timings)
        explain_ms, plan_ms, pages, plan = explain(cur, sql)
        (PLANS / phase / f"{qid}.txt").write_text(
            f"-- {variant}: {title}\n-- rows returned: {rows}\n"
            f"-- wall-clock median of {RUNS} runs: {median:.2f} ms\n\n{sql}\n\n{plan}\n", encoding="utf-8")
        out[qid] = {"title": queries[qid][0], "variant": variant, "rows": rows, "fingerprint": fingerprint,
                    "exec_ms": median, "explain_ms": explain_ms, "plan_ms": plan_ms, "pages": pages,
                    "top_scan": plan.splitlines()[0].split("  (")[0].strip()}
        print(f"  {phase:<6} {qid:<4} {median:>10.2f} ms  {pages:>8} pages  rows={rows}")
    return out


WRITE_TESTS = {
    # name -> INSERT that copies existing rows; run inside a transaction that is rolled back
    "invoice_items +50,000 rows": """
        INSERT INTO hospital.invoice_items (invoice_id, item_type, description, quantity, unit_price)
        SELECT invoice_id, item_type, description, quantity, unit_price
        FROM hospital.invoice_items ORDER BY invoice_item_id LIMIT 50000""",
    "encounters +20,000 rows": """
        INSERT INTO hospital.encounters (patient_id, doctor_id, encounter_type, encounter_date, chief_complaint)
        SELECT patient_id, doctor_id, 'Emergency', encounter_date, chief_complaint
        FROM hospital.encounters ORDER BY encounter_id LIMIT 20000""",
}


def write_cost(cur) -> dict[str, float]:
    """Median time of bulk inserts into tables that gained indexes (rolled back)."""
    out = {}
    for name, sql in WRITE_TESTS.items():
        timings = []
        for _ in range(3):
            cur.execute("BEGIN")
            start = time.perf_counter()
            cur.execute(sql)
            timings.append((time.perf_counter() - start) * 1000)
            cur.execute("ROLLBACK")
        out[name] = statistics.median(timings)
        print(f"  write  {name:<28} {out[name]:>8.1f} ms")
    return out


def main() -> None:
    queries = load_queries()
    RESULTS.mkdir(exist_ok=True)
    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("SET jit = off")
        version = cur.execute("SHOW server_version").fetchone()[0]

        print("Phase 1: baseline (Portfolio 1 indexes only)")
        cur.execute((SQL / "03_reset_optimisations.sql").read_text(encoding="utf-8"))
        before = run_phase(cur, "before", queries)
        writes_before = write_cost(cur)

        print("Phase 2: applying optimisations")
        cur.execute((SQL / "02_optimisations.sql").read_text(encoding="utf-8"))
        created = re.findall(r"CREATE (?:UNIQUE )?(?:INDEX|MATERIALIZED VIEW) (\w+)",
                             (SQL / "02_optimisations.sql").read_text(encoding="utf-8"))
        idx = cur.execute("""
            SELECT c.relname, t.relname, pg_size_pretty(pg_relation_size(c.oid)), pg_relation_size(c.oid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_index i ON i.indexrelid = c.oid
            LEFT JOIN pg_class t ON t.oid = i.indrelid
            WHERE n.nspname = 'hospital' AND c.relname = ANY(%s)
            ORDER BY pg_relation_size(c.oid) DESC""", (created,)).fetchall()
        after = run_phase(cur, "after", queries)
        writes_after = write_cost(cur)

    # ---- CSV ----
    with open(RESULTS / "benchmark_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "title", "after_variant", "rows", "same_result", "before_ms", "after_ms", "speedup",
                    "before_pages", "after_pages", "before_plan_root", "after_plan_root"])
        for q in before:
            b, a = before[q], after[q]
            w.writerow([q, b["title"], a["variant"], b["rows"], b["fingerprint"] == a["fingerprint"],
                        f"{b['exec_ms']:.3f}", f"{a['exec_ms']:.3f}",
                        f"{b['exec_ms'] / a['exec_ms']:.1f}", b["pages"], a["pages"], b["top_scan"], a["top_scan"]])

    # ---- Markdown summary ----
    lines = [f"# Benchmark summary", "",
             f"Run: {datetime.now():%Y-%m-%d %H:%M}, PostgreSQL {version}, "
             f"wall-clock median of {RUNS} warm runs, JIT off.", "",
             "| Query | Purpose | Rows | Same result | Before (ms) | After (ms) | Speed-up | Pages before | Pages after |",
             "|---|---|---:|:---:|---:|---:|---:|---:|---:|"]
    for q in before:
        b, a = before[q], after[q]
        tag = " (rewritten)" if a["variant"] != q else ""
        same = "yes" if b["fingerprint"] == a["fingerprint"] else "**NO**"
        lines.append(f"| {q}{tag} | {b['title']} | {b['rows']} | {same} | {b['exec_ms']:.2f} | {a['exec_ms']:.2f} | "
                     f"{b['exec_ms'] / a['exec_ms']:.1f}x | {b['pages']:,} | {a['pages']:,} |")
    lines += ["", "Pages = shared buffer pages (8 KB) touched by the whole plan.", "",
              "## Storage cost of the optimisations", "", "| Object | On table | Size |", "|---|---|---:|"]
    lines += [f"| `{name}` | {tbl or '(materialized view)'} | {size} |" for name, tbl, size, _ in idx]
    lines.append(f"| **Total** | | **{sum(r[3] for r in idx) / 1024 / 1024:.1f} MB** |")
    lines += ["", "## Write cost of the optimisations", "",
              "Bulk INSERT inside a rolled-back transaction, median of 3.", "",
              "| Insert | Before (ms) | After (ms) | Change |", "|---|---:|---:|---:|"]
    for name in WRITE_TESTS:
        b, a = writes_before[name], writes_after[name]
        lines.append(f"| {name} | {b:.1f} | {a:.1f} | {100 * (a - b) / b:+.0f}% |")
    (RESULTS / "benchmark_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Results written to {RESULTS}")


if __name__ == "__main__":
    main()
