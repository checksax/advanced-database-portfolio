# Portfolio 4 — NoSQL and Advanced Data Models

**Case study:** St. Grace Specialist Hospital (the same data as Portfolios 1–3)
**NoSQL system:** MongoDB 9.0.4 (standalone, Windows service), Python driver `pymongo` 4.10.1

The relational database from Portfolio 1 was remodelled for MongoDB and migrated
automatically. The same hospital is represented with **four NoSQL data models**:

| Model | Collection(s) | Hospital use |
|---|---|---|
| **Document** | `patients`, `encounters`, `drugs`, `staff` | Patient record and the full clinical chart of each visit |
| **Time series** | `vitals` | 4-hourly inpatient observations (temperature, pulse, SpO₂, BP) |
| **Graph** (adjacency list + `$graphLookup`) | `referrals` | Which doctors refer patients to which departments |
| **Key-value** | `cache` (TTL), `queue` | Cached patient summaries that expire; the clinic check-in queue |

## Contents

| File | Purpose |
|---|---|
| `migrate.py` | Reads PostgreSQL, builds documents, creates validated collections and indexes, loads MongoDB |
| `queries.py` | 11 queries (M1–M10, plus M3b), explain plans, comparison with PostgreSQL, sample export |
| `features_demo.py` | Validation, schema evolution, polymorphism, atomicity, concurrency, transactions, TTL, queue |
| `common.py` | Connection settings |
| `samples/*.json` | Real documents from the database (patient, encounters, drug, referral, vitals) |
| `plans/*.json` | Full `explain("executionStats")` output for every query |
| `results/` | Migration log, query results, feature-demo output |

## 1. Data model

### From 22 tables to one chart per visit

In Portfolio 1, one outpatient visit is spread over **9 tables**: encounters, patients,
staff, doctors, departments, diagnoses, icd10_codes, prescriptions + items, lab orders +
results, invoices. A doctor opening a chart needs all of it at once. In MongoDB each visit
is **one `encounters` document** (see `samples/encounter_inpatient_with_culture.json`):

```text
encounter
├─ type, date, chief_complaint
├─ patient   {patient_id, hospital_no, name, gender, age_at_visit}     ← extended reference
├─ doctor    {staff_id, name, specialty, department}                   ← extended reference
├─ admission {ward, bed, admitted_at, discharged_at}                   (inpatients only)
├─ vitals    {temperature_c, blood_pressure{systolic, diastolic}, pulse_bpm, weight_kg}
├─ diagnoses [ {code, description, category, type} ]                   ← ICD text copied in
├─ prescription {status, dispensed_at, items[ {drug, strength, dose, quantity, dispensed} ]}
├─ labs      [ {test_code, priority, status, result{ …shape depends on the test… }} ]
└─ billing   {invoice_no, total, paid, balance, status}                ← computed summary
```

### Embed or reference? The decisions

| Relationship | Decision | Reason |
|---|---|---|
| Visit → diagnoses, prescription items, lab results | **Embed** | Always read with the visit, owned by it, bounded in size (at most a handful each) |
| Visit → doctor, patient | **Extended reference** (id + the fields shown on a chart) | Avoids a lookup on every read. The copied fields (name, specialty) rarely change |
| Patient → visits | **Reference** + **subset** of the 3 most recent | A frequent patient has 293 visits. Embedding them all would make an unbounded, ever-growing document (16 MB limit) |
| Patient → stats (visit count, total billed, outstanding) | **Computed pattern** | Pre-calculated at write time, so reception doesn't aggregate thousands of rows |
| Patient → allergies, insurance | **Embed** | Tiny, always needed, and safety-critical at the point of prescribing |
| Drug-specific properties (cold chain, high-alert, route…) | **Attribute pattern** `[{k, v}]` | Each drug has different properties; one index on `attributes.k/v` covers them all |
| Lab results | **Polymorphic sub-documents** | A haemoglobin value, a full blood count panel, a malaria smear and a urine culture with antibiotic sensitivities have different shapes |
| Doctor ↔ doctor referrals | **Separate edge collection** | Many-to-many and traversed recursively. That's graph data, not something to embed |
| Vitals | **Time-series collection** | High-volume append-only measurements; MongoDB buckets and compresses them automatically |

### Not schemaless: validation

`patients` and `encounters` have `$jsonSchema` validators (`validationLevel: strict`):
required fields, enums (gender, genotype, blood group, encounter type, billing status),
regular expressions (hospital number `^SGH-[0-9]{7}$`, Nigerian phone format), and numeric
ranges (temperature 30–45 °C). All 222,743 migrated documents passed. Money is stored as
**Decimal128**, never as binary floating point.

### Migration (`results/migration_output.txt`)

| Collection | Documents | Avg size | Data | On disk (compressed) |
|---|---:|---:|---:|---:|
| encounters | 172,743 | 1.5 KB | 247.4 MB | 70.8 MB |
| patients | 50,000 | 752 B | 35.9 MB | 12.8 MB |
| referrals | 13,008 | 217 B | 2.7 MB | 0.7 MB |
| vitals (time series) | 24,771 readings | – | 0.5 MB | **0.2 MB** |
| staff / drugs | 360 / 20 | – | – | – |

Total load time: 81 s. The document model is larger before compression than the
normalised tables, because names, ICD descriptions and drug names are repeated in every
visit. WiredTiger's compression (about 3.5×) removes most of that cost on disk.

**Synthetic additions.** The relational data stores one value per lab result. To show
schema flexibility, the migration adds an FBC panel (WBC, platelets), malaria species and
density, and urine-culture antibiotic sensitivities. These are generated deterministically
and are as fictitious as the rest of the data.

## 2. Queries and performance (`results/query_results.md`)

| # | Question | Model / feature | Time | Docs examined | Plan |
|---|---|---|---:|---:|---|
| M1 | Full history of patient SGH-0000001 (293 visits) | single-collection read | 5.3 ms | 293 | IXSCAN `patient_history` |
| M2 | Weekly malaria cases, last 13 weeks | multikey index on `diagnoses.code` | 32.8 ms | 2,867 | IXSCAN `diagnosis_date` |
| M3 | Critical lab results, last 7 days + patient phone | partial index + `$lookup` | 24.2 ms | 154 | IXSCAN `critical_labs` |
| M3b | Same, without the `$lookup` | – | **3.0 ms** | 78 | IXSCAN `critical_labs` |
| M4 | Case-insensitive name search (`okafor`, `ng…`) | collation index (strength 2) | 1.4 ms | 11 | IXSCAN `name_ci` |
| M5 | Revenue by department and month | `$group` on embedded billing | 484 ms | 86,338 | IXSCAN `date` |
| M6 | Top 10 drugs dispensed, 90 days | `$unwind` of embedded items | 200 ms | 21,377 | IXSCAN `date` |
| M7 | Antibiotic resistance by organism | query into polymorphic results | 442 ms | 172,743 | COLLSCAN |
| M8 | Inpatients with 12-h moving-average temp > 38 °C | time series + `$setWindowFields` | 15.8 ms | 408 buckets | CLUSTERED_IXSCAN |
| M9 | Departments reachable via referral chains (≤ 3 hops) | graph, `$graphLookup` | 133 ms | – | – |
| M10 | Get cached patient summary by key | key-value | 0.9 ms | 1 | EXPRESS_IXSCAN |

### Same questions, both databases

`queries.py` asks MongoDB the same questions as Portfolio 2's SQL Q3, Q7 and Q8, and checks
that the answers agree. They agreed every time (293 visits, identical weekly counts, 76
critical results).

| Question | MongoDB, indexed | MongoDB, forced COLLSCAN | PostgreSQL | Same answer |
|---|---:|---:|---:|:---:|
| Patient history | 5–16 ms | 186–426 ms | 6–17 ms | ✅ |
| Weekly malaria cases | 31–91 ms | 224–610 ms | 19–52 ms | ✅ |
| Critical labs + phone | 19–42 ms | 318–616 ms | 2–4 ms | ✅ |

The ranges come from four runs. This laptop's timings varied by up to 2× between runs, for
both databases, so I only draw conclusions from consistent patterns:

- **The patient chart is a tie.** MongoDB reads 293 pre-joined documents; PostgreSQL does
  a 5-table join through indexes. Both finish in single-digit to low-teens milliseconds. The
  document model's advantage here is simplicity (one query, no joins), not raw speed at
  this scale.
- **Aggregations favour PostgreSQL (~2×).** M2 must `$unwind` diagnosis arrays and group;
  SQL aggregates narrow rows. A MongoDB document carries the whole chart even when the
  query needs only two fields.
- **Joins cost MongoDB the most (~10×).** M3 is the only query that needs `$lookup`, to
  fetch the phone number from `patients`. Removing it (M3b) drops the time from 24 ms to
  **3 ms**, on par with PostgreSQL. So if the hospital calls patients about critical
  results, the phone should be added to the encounter's `patient` extended reference.
  The price is that a phone-number change must then be written to all of that patient's
  encounters.
- **Indexes matter as much as in SQL.** Forcing a collection scan made the same queries
  14–35× slower, as in Portfolio 2. The partial index (`critical_labs`, only documents with
  a Critical result) and the collation index (case-insensitive search without the
  `lower()` expression index Portfolio 2 needed) carry over directly.

### What only the NoSQL models did easily

- **M7 Antibiotic resistance:** queries `labs.result.culture.sensitivities`, a structure
  that only exists for some test types. In the relational schema this would need new tables
  (`culture_results`, `sensitivities`) and a migration. Here the results simply have a
  different shape. The ~20% resistance figures reflect how the synthetic data was
  generated, not real epidemiology.
- **M8 Fever surveillance:** a 12-hour moving average per admission with
  `$setWindowFields` over a time-series collection. 24,771 readings are stored in buckets
  using 0.2 MB on disk, and only 408 buckets were read.
- **M9 Referral graph:** from one General Medicine doctor, `$graphLookup` follows referral
  edges up to 3 hops: 251 direct referrals (O&G 90, Cardiology 85, Surgery 76), then
  thousands of edges at hops 1–2. That growth is typical of graph traversal. MongoDB can do
  it, but a native graph database (Neo4j) would store adjacency directly and handle deeper
  or weighted traversals (shortest path, centrality) far better.

## 3. Behaviour (`results/features_demo_output.txt`)

| Demo | What happened |
|---|---|
| **F1 Validation** | 3 invalid documents rejected (code 121), with reasons: `gender: value was not found in enum (value 'X')`, `contact: phone: regular expression did not match (value '12345')`, `missing ['date_of_birth']` |
| **F2 Schema evolution** | A new NHIA enrolee-ID field was added to one patient. No `ALTER TABLE`, and the other 49,999 documents were untouched |
| **F3 Polymorphism** | 6 different lab-result shapes coexist in one collection, e.g. 91,558 numeric results, 52,528 FBC panels, 4,158 cultures with sensitivity arrays |
| **F4 Single-document atomicity** | A prescription's status and all 4 item quantities were updated in **one** atomic write; a second pharmacist's attempt matched 0 documents. Portfolio 3 needed a 2-table transaction for this |
| **F5 Concurrency** | 20 parallel threads each ran `{stock ≥ 10} → $inc −10` on stock 100: exactly 10 succeeded, 10 were refused, final stock 0. **No lost update, no transaction needed** (compare Portfolio 3, C1a) |
| **F6 Multi-document transaction** | **Failed: code 20, "Transaction numbers are only allowed on a replica set member or mongos".** Transactions need the replication oplog. Portfolio 5 sets up a replica set and repeats this |
| **F7 TTL cache** | A summary set to expire after 5 s was removed automatically by the TTL monitor, which runs every 60 s, so expiry is approximate |
| **F8 Atomic queue** | Two nurses called 12 patients concurrently with `findOneAndUpdate`: nobody was called twice and nobody was missed |

## 4. Suitability: which model for which part of the hospital?

| Workload | Best fit | Why |
|---|---|---|
| Billing, payments, stock, bed allocation | **Relational (PostgreSQL)** | Multi-row invariants, multi-entity transactions, constraints (EXCLUDE, FK) and ad-hoc financial reporting. Portfolio 3 showed how much of its correctness comes from the database |
| Clinical chart / patient-history viewing | **Document (MongoDB)** | Read as a whole; varied, evolving content (new tests, new forms); one read, no joins |
| Lab results from many analysers | **Document** | Each test type has its own structure; new tests need no migration |
| Bedside monitors, vital signs | **Time series** (or a wide-column store such as Cassandra at very high volume) | Append-only, time-ordered, queried by window; here about 8 bytes on disk per reading |
| Referral networks, infection contact tracing | **Graph** (Neo4j for real use) | Recursive relationships; questions about paths and reachability |
| Sessions, cached summaries, queues | **Key-value** (Redis in production) | Lookup by key, automatic expiry, atomic pop |

**Recommendation:** polyglot persistence. Keep PostgreSQL as the system of record for
anything financial or capacity-related. Use a document store for the clinical record, fed
from PostgreSQL (as `migrate.py` does in batch; in production via change-data-capture).
Use time-series storage for monitor data. A column-family store such as Cassandra wasn't
installed. The vitals model would map onto it as a table partitioned by
`(admission_id, day)` and clustered by timestamp, which suits millions of readings per day
across many sites. The cost of polyglot persistence is keeping copies consistent, which is
the subject of Portfolio 5.

## Limitations

- **Standalone server:** no replication, so no multi-document transactions (F6) and no
  failover. Portfolio 5 covers both.
- **No dedicated graph, key-value or wide-column engine was installed.** MongoDB's
  `$graphLookup`, TTL indexes and time-series collections show those models. The analysis
  above notes where a specialised engine would be better.
- **Dates** are stored as hospital local time, but BSON displays them with a `Z` (UTC)
  suffix.
- **Copied fields go stale:** the extended references (doctor name, patient name) are
  correct at migration time. In a live system, a name change would need an update across
  documents. That is the trade-off of denormalisation.

## How to run

```powershell
pip install "pymongo==4.10.1"
python migrate.py                                         # about 1.5 min, rebuilds the MongoDB database
python queries.py                                         # writes results/query_results.md, plans/, samples/
python features_demo.py > results\features_demo_output.txt
```
