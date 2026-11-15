# MIT 8103 Advanced Database Systems — Portfolio Final Documentation

Repository copy of the 3-page LMS submission
(`MIT8103_Portfolio_Final_Documentation_3pages.docx`, which also contains the Portfolio 2 chart).

All five activities use one case study, a simulated hospital; every result is backed by scripts and captured output in the GitHub repository.

**Student:** [name] · **Matric no.:** [number] · **Programme:** MIT · **GitHub:** [link] · **Software:** PostgreSQL 18.6, MongoDB 9.0.4, Python 3.12

## Selected case study

**St. Grace Specialist Hospital** is a simulated Lagos hospital (10 departments, 108 beds) used by records officers, doctors, nurses, lab scientists, pharmacists and cashiers. It stores patients, appointments, visits, diagnoses, prescriptions, stock, lab results, admissions and billing; a seeded generator produced 2.6 million fictitious rows (50,000 patients).

## Portfolio 1 — Database design and modelling

A 22-table PostgreSQL schema in 3NF/BCNF enforces the hospital's rules: an `EXCLUDE` constraint blocks overlapping doctor bookings, partial unique indexes allow one current patient per bed, and 13 invalid inserts were each rejected by the right constraint. Normalisation was worked from a paper prescription slip (UNF) to BCNF.

## Portfolio 2 — Query processing and optimisation

Ten daily hospital queries were benchmarked before and after optimisation; all ten returned identical results, seven got 5× to 972× faster, and three candidate indexes were dropped because they gave no gain. (Chart: in the .docx; numbers: `portfolio-2-query-optimisation/results/benchmark_summary.md`.)

Techniques: composite, expression, partial and covering indexes, a `LATERAL` rewrite, a materialized view and partition pruning. Q9's real cost was parallel-worker startup (314 vs 136 ms), not a missing index.

## Portfolio 3 — Transactions and concurrency

Six anomalies were reproduced with two live sessions and then prevented: a lost stock update (relative UPDATE or `FOR UPDATE`), a non-repeatable bill read and a phantom booking (REPEATABLE READ), write skew on the last ICU bed (only SERIALIZABLE stopped it), a double payment (invoice row lock) and a deadlock (consistent lock order). COMMIT, ROLLBACK, SAVEPOINT and MVCC were shown in single-session scripts.

## Portfolio 4 — NoSQL and advanced data models

The hospital was remodelled in MongoDB as document, time-series, graph (`$graphLookup`) and key-value collections (261,000 documents). Both databases gave the same answers to the same questions; PostgreSQL was faster on aggregations, and one MongoDB query was 10× slower only because of a `$lookup` join. MongoDB suits the varied clinical chart; PostgreSQL stays the system of record for billing, stock and beds.

## Portfolio 5 — Distributed and cloud database

A 3-member MongoDB replica set elected a new primary 16.5 s after a crash, with 0 of 166 bookings lost; with 2 of 3 members down it refused writes but kept serving reads (CAP). Transactions that failed in Portfolio 4 worked here. A hashed patient key split data 49.9 / 50.1 % across shards, zones kept each branch's patients on its own shard, and a cloud design spreads PostgreSQL and MongoDB across three availability zones.

## Evidence in the repository

Each folder's README explains method and results, with plans, run logs and outputs beside the scripts. **Screenshots:** [insert pgAdmin ERD, Q1 plan before/after, a lock wait, MongoDB Compass]

## Individual technical reflection

> **DRAFT — rewrite in your own words.**

Keeping rules in the database paid off: the booking constraint also acted as concurrency control. Measuring honestly was the hardest part: laptop timings varied up to 2×, three "obvious" indexes gave no gain, and a sharding bug only showed in `explain()`. [Add what you learned.]

## AI use declaration

**Claude Code** (Anthropic, Claude Opus 5.5) planned the work, wrote and ran the SQL and Python scripts on my machine, and drafted the READMEs and this document. Built-in checks (constraint tests, identical-result fingerprints, matching SQL and MongoDB answers) caught several of its errors. **My review:** [what you re-ran, read and corrected]. I remain responsible for this work.

## References

1. Berenson, H. et al. (1995). A critique of ANSI SQL isolation levels. *Proc. ACM SIGMOD*, 1–10.
2. Elmasri, R. & Navathe, S. B. (2016). *Fundamentals of Database Systems* (7th ed.). Pearson.
3. Kleppmann, M. (2017). *Designing Data-Intensive Applications*. O'Reilly.
4. PostgreSQL 18 Documentation. https://www.postgresql.org/docs/18/
5. MongoDB Manual. https://www.mongodb.com/docs/manual/
