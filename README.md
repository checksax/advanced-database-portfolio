# Advanced Database Systems Portfolio — MIT 8103

**Case study:** St. Grace Specialist Hospital, a hospital management system (simulated, fictitious data)

The summary submitted to the LMS is [final-documentation/FINAL_DOCUMENTATION.md](final-documentation/FINAL_DOCUMENTATION.md).
Each portfolio folder has its own README with the method, results and analysis.

| Folder | Portfolio activity | Marks | Headline result |
|---|---|---|---|
| [`portfolio-1-database-design/`](portfolio-1-database-design/) | Database Design and Modelling | 7 | 22 tables in 3NF/BCNF; 15 constraint tests behave as designed, incl. an EXCLUDE constraint against double booking |
| [`portfolio-2-query-optimisation/`](portfolio-2-query-optimisation/) | Query Processing and Optimisation | 7 | 10 queries benchmarked; 7 got 5×–970× faster with identical results; 3 indexes dropped on evidence |
| [`portfolio-3-transactions-concurrency/`](portfolio-3-transactions-concurrency/) | Transactions and Concurrency | 7 | 6 anomalies reproduced with live sessions (incl. write skew) and each prevented |
| [`portfolio-4-nosql/`](portfolio-4-nosql/) | NoSQL and Advanced Data Models | 7 | Document, time-series, graph and key-value models in MongoDB; same answers as SQL |
| [`portfolio-5-distributed-cloud/`](portfolio-5-distributed-cloud/) | Distributed and Cloud Database Exercise | 6 | Replica-set failover in 16.5 s with 0 lost writes; sharding with branch zones; cloud design |
| [`final-documentation/`](final-documentation/) | Summary, reflection, AI-use declaration | 6 | |
| [`data-generator/`](data-generator/) | Synthetic data (about 2.6 million rows, fixed seed) | – | |

## Case study statement

- **Organisation:** St. Grace Specialist Hospital, a multi-department private hospital (10 departments, 6 wards, 108 beds).
- **Nature of system:** hospital management covering outpatient clinics, emergency, inpatient wards, pharmacy, laboratory and billing.
- **Users:** administrators and records officers, doctors, nurses, lab scientists, pharmacists and cashiers.
- **Major data:** patients, staff, departments, appointments, encounters, diagnoses, prescriptions, drugs and stock, lab orders and results, wards, beds, admissions, invoices, payments.
- **Key operations:** register patient, book appointment, record diagnosis, prescribe and dispense drugs, order lab tests and record results, admit and discharge, bill and pay, retrieve patient history.

## Environment

| Service | Address | Used by |
|---|---|---|
| PostgreSQL 18.6 | `localhost:5432`, db `stgrace_hospital`, schema `hospital`, user `hospital_admin` / `hospital_pass` | Portfolios 1–4 |
| MongoDB 9.0.4 (Windows service) | `localhost:27017`, db `stgrace_hospital` | Portfolio 4 |
| MongoDB replica set `rs0` | `127.0.0.1:27018–27020` (started by `portfolio-5-distributed-cloud/cluster.py`) | Portfolio 5 |
| MongoDB sharded cluster | config `27030`, shards `27031–27032`, mongos `27040` | Portfolio 5 |

The credentials are local development values only. Python dependencies: `pip install "psycopg[binary]==3.2.3" "pymongo==4.10.1"`.

## Run order

1. `portfolio-1-database-design`: `psql -U postgres -f sql/00_create_database.sql` once, then `run_portfolio1.ps1`
2. `portfolio-2-query-optimisation`: `python benchmark.py`, then `sql/04_advanced_techniques.sql` and `sql/05_final_index_set.sql`
3. `portfolio-3-transactions-concurrency`: `sql/01_transaction_objects.sql`, `sql/02_transaction_basics.sql`, `python concurrency_demos.py`
4. `portfolio-4-nosql`: `python migrate.py`, `python queries.py`, `python features_demo.py`
5. `portfolio-5-distributed-cloud`: `python cluster.py start rs` → `python replication_demo.py`; `python cluster.py start shard` → `python sharding_demo.py`

`docker-compose.yml` is an alternative, container-based setup that was not used; starting it
alongside the native PostgreSQL would clash on port 5432.

`portfolio-5-distributed-cloud/cluster/data/` (about 1.5 GB of cluster data files) should not
be committed: add it to `.gitignore`.
