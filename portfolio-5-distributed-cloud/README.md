# Portfolio 5 — Distributed and Cloud Database Exercise

**Case study:** St. Grace Specialist Hospital (data from Portfolios 1 and 4)
**Platform:** MongoDB 9.0.4. Two real local clusters were built and tested:

1. a **3-member replica set** (replication, consistency, failover, transactions)
2. a **sharded cluster** with a config server, two shards and a `mongos` router
   (horizontal scaling, query routing, zones)

A production design for the hospital is in
[cloud-deployment-design.md](cloud-deployment-design.md).

## Contents

| File | Purpose |
|---|---|
| `cluster.py` | Starts, stops and inspects both clusters (`python cluster.py start rs`, `start shard`, `stop …`, `status …`) |
| `replication_demo.py` | R0–R6: topology, replication, write concerns, lag and consistency, transactions, failover, majority loss |
| `sharding_demo.py` | S0–S5: hashed sharding, query routing, zone sharding, hot-shard problem, shard failure |
| `cloud-deployment-design.md` | Production architecture, consistency settings per workload, sharding plan, security and operations |
| `results/replication_demo_output.txt` | Timestamped output of the replication run |
| `results/sharding_demo_output.txt` | Timestamped output of the sharding run |
| `cluster/logs/` | Server logs of every cluster member: 6 successful elections and 4 step-downs across the run, and **0 rollbacks** (no acknowledged majority write was ever undone) |

## Lab topology

```text
 Replica set "rs0"                         Sharded cluster
 ┌──────────────┐                          ┌─────────┐   application connects here
 │ :27018  P=2  │ primary                  │ mongos  │ :27040 (router)
 │ :27019  P=1  │ secondary                └────┬────┘
 │ :27020  P=1  │ secondary              ┌──────┼──────────┐
 └──────────────┘                        ▼      ▼          ▼
  oplog replication,                   cfg   shardA      shardB
  heartbeats every 2 s,              :27030  :27031      :27032
  election timeout 10 s            (metadata) (LAGOS zone) (ABUJA zone)
```

Every member is a separate `mongod` process with its own data directory, bound to
127.0.0.1, with a 256 MB cache so that both clusters fit on an 8 GB laptop. They ran
one at a time, alongside PostgreSQL and the Portfolio 4 MongoDB service.

## Part A — Replication (`results/replication_demo_output.txt`)

| # | Experiment | Result |
|---|---|---|
| R0 | Topology | 3 voting members; majority = 2, so the set tolerates **1** failure. Member :27018 has priority 2 (preferred primary) |
| R1 | Load 50,000 patients + 43,915 encounters through the primary (w:majority) | All three members report the **identical `dbHash`** (`ae5c25eacb3f…`). The oplog stores an update as an idempotent diff: `{'$v': 2, 'diff': {'scontact': {'u': {'phone': …}}}}` |
| R2 | 200 inserts per write concern | w:0 **0.43 ms** · w:1 **3.32 ms** · w:1+j **4.25 ms** · w:majority **5.23 ms** · w:3 **4.81 ms** (median) |
| R3 | Pause replication on :27020 (fail point) | :27020 returned the **old phone number** (stale read). w:majority still succeeded (2 of 3). **w:3 timed out** after 3 s. A **causal-consistency** read on :27020 waited and timed out instead of returning stale data. After resuming, :27020 caught up (eventual consistency) |
| R4 | Multi-document transactions | Abort: both documents unchanged. Commit: invoice Paid **and** patient outstanding −₦24,500 together. Concurrent update → **WriteConflict (112)**, labelled `TransientTransactionError` for retry. (The same transaction **failed** on the standalone server in Portfolio 4, F6) |
| R5 | **Kill the primary process** while a client books an appointment every 50 ms | New primary elected **16.5 s** after the crash. **166 of 166 bookings succeeded**; the slowest waited 16 s, then was retried automatically. 0 lost. The crashed member rejoined as secondary in 14.6 s, caught up from the oplog and, having priority 2, took primary back after 25 s |
| R6 | Stop 2 of 3 members (majority lost) | The surviving primary **stepped down** within 1.1 s. **Writes refused**, but **reads still served** with `secondaryPreferred`. Restarting the two members restored a primary in 18.3 s |

### Analysis

**Replication model.** MongoDB uses single-leader (primary/secondary) asynchronous
replication. The primary records every change in the **oplog**, a capped collection here
limited to 256 MB, of which 109 MB was used after loading. Secondaries pull and re-apply
it. Oplog entries are idempotent diffs, not the original statements, so re-applying one is
safe. That's what lets a member that crashed mid-way recover by replaying from its last
applied position (R5). The **oplog window** (here 142 s of heavy loading) limits how long
a member can be offline and still catch up without a full resync. Production monitoring
should alert when it drops below a day.

**Write concern is a dial between latency and durability (R2).** `w:1` acknowledges once
the primary has the write in memory. If the primary then crashes before replicating, that
write is **rolled back** when the old primary rejoins. `w:"majority"` costs only about 2 ms
more here, and a majority-acknowledged write survives any single failure. That is why R5
lost nothing. `w:3` adds no safety beyond majority, and it **blocks whenever any member is
slow or down** (R3 timeout). It trades availability for nothing, so it is not recommended.
On a real network, majority latency includes a round trip to another availability zone
(typically 1–2 ms), not the near-zero loopback measured here.

**Consistency models observed (R3).**

| Guarantee | Mechanism | Observed |
|---|---|---|
| Eventual consistency | default reads from a secondary | stale phone number, correct after catch-up |
| Read-your-writes / causal | causal-consistency session (`afterClusterTime`) | lagging member **waited** rather than answer stale |
| No rollback-able reads | `readConcern: "majority"` | (used in R4 transactions with `snapshot`) |
| Strong (linearizable) | primary reads with `w:majority` + `readConcern: "linearizable"` | not needed for this workload |

**Failover timing (R5).** 16.5 s ≈ the 10 s `electionTimeoutMillis` (secondaries must
miss heartbeats for that long before suspecting the primary) + about 2 s heartbeat
interval + election and the client noticing the new primary. Lowering the timeout gives
faster failover but more **false elections** on a congested network. The default suits a
hospital. Two client settings made the outage invisible to users apart from a pause:
**retryable writes** and a server-selection timeout (30 s) longer than an election.

**CAP in practice (R6).** With the majority lost, the remaining member couldn't rule out
that the other side of a partition had elected a primary, so it **stepped down**. Accepting
writes risked two primaries (split brain) and conflicting bills. MongoDB with majority
writes is therefore **CP** for writes. Reads remained **available**, but possibly stale, via
`secondaryPreferred`. For a hospital that is the right default: a doctor can still read a
chart during an outage, but no two cashiers can each "settle" the same bill on opposite
sides of a network split.

## Part B — Sharding (`results/sharding_demo_output.txt`)

| # | Experiment | Result |
|---|---|---|
| S1 | Shard `encounters` on `hash(patient.patient_id)`, insert 43,915 | **21,904 / 22,011** documents (49.9 % / 50.1 %) |
| S2 | Query routing | By patient_id (shard key): **SINGLE_SHARD**, 1 shard. By hospital_no (not the key): **SHARD_MERGE_SORT**, both shards. Weekly malaria aggregation: split into `$match`+`$group` on **each shard**, then `$mergeCursors`+`$group`+`$sort` on the router |
| S3 | **Zone sharding** `patients` on `{branch, _id}`; LAGOS → shardA, ABUJA → shardB | 31,565 Lagos patients all on shardA, 18,435 Abuja patients all on shardB. `{branch: 'ABUJA'}` → **SINGLE_SHARD** on shardB |
| S4 | Same 2,000 new appointments, range key on `scheduled_start` vs hashed | Range: **0 / 2000**, every insert hit one shard (hot shard). Hashed: **1037 / 963**. But a "bookings on 4 Nov" query is targeted with the range key and scatter-gather with hashed |
| S5 | Stop shardB | Targeted query for a shardA patient: **OK**. Cluster-wide count: **failed**. `allowPartialResults`: **21,904 of 43,915** (shardA's half): degraded but available |

### Analysis

**Choosing a shard key is the most important sharding decision.**
- **Cardinality and distribution:** hashing `patient_id` (50,000 values) gave a near-perfect
  split (S1).
- **Query isolation:** the most frequent query, "this patient's chart", includes the key,
  so it goes to **one** shard (S2). Queries without the key (hospital number, diagnosis,
  date) are scatter-gather, and their cost grows with every shard added. For the
  hospital-number lookup, the fix is to resolve hospital_no → patient_id first (it is
  unique), then query by the key.
- **Write distribution:** a monotonically increasing key (date, ObjectId) sends all new
  writes to the last chunk (S4: 2000 of 2000 on one shard). Adding shards then adds no
  write capacity. Hashing fixes that but loses targeted range queries. That trade-off can't
  be avoided, only chosen.

**Zones put data where it belongs (S3).** With `branch` leading the shard key and zones
pinned to shards, each branch's patients are physically stored on its own shard. In
production that shard's servers would be near (or in) that branch, giving lower latency and
a mechanism for data-residency rules. One detail mattered. My first attempt defined the
ABUJA zone as ending at `{branch: 'ABUJA', _id: MaxKey}`, and a query on `branch: 'ABUJA'`
was then sent to **both** shards: a chunk boundary at that point is the *inclusive* start of
the neighbouring LAGOS-side chunk. Defining the zones to tile the key space
(`ABUJA` up to `{LAGOS, MinKey}`) made the query single-shard. This kind of detail only
shows up by testing with `explain()`.

**Sharding is not high availability (S5).** Sharding splits data so that each shard holds
*different* data. When a shard is lost, its data is unavailable. Replication is what keeps
each shard alive, so production uses both: every shard is a 3-member replica set (this lab
used 1-member shards to fit in memory).

**How the pieces fit together.** The config server holds the chunk map; `mongos` caches
it and routes each query; shards store data and run their part of aggregations; the
balancer moves chunks to even out load (stopped during S4 so the demonstration stayed
deterministic).

## Relational equivalents (PostgreSQL)

The same concepts exist for the hospital's PostgreSQL system of record. I did not build
them here, to avoid reconfiguring the PostgreSQL service used by Portfolios 1–3.

| MongoDB (demonstrated) | PostgreSQL equivalent |
|---|---|
| Oplog replication to secondaries | WAL streaming replication to standbys |
| `w:1` / `w:"majority"` | `synchronous_commit = local` / `on` with `synchronous_standby_names = 'ANY 1 (…)'` |
| Secondary reads (`secondaryPreferred`) | Hot-standby read replicas (`hot_standby = on`) |
| Automatic election | External manager (Patroni) or a managed service (RDS / Azure / Cloud SQL HA) |
| Causal consistency | `remote_apply`, or read from the primary |
| Sharding by hashed key | Citus (distribution column `patient_id`), or partitioning + foreign data wrappers |
| Zones | List partitioning by `branch` with partitions on different tablespaces or servers |

## Limitations

- **One machine.** All members share one CPU, disk and memory, and talk over loopback, so
  replication lag and w:majority latency are lower than across real availability zones.
  Failure modes (crash, majority loss, shard loss) and timings driven by protocol timeouts
  (elections) are realistic.
- **Simulated lag.** Replication lag in R3 was created with the `stopReplProducer` fail
  point, which requires `enableTestCommands`. That is a test-only server parameter, used
  here deliberately and never in production.
- **1-member shards** and a 1-member config server, to fit in 8 GB RAM. Production would
  use 3 members each.
- **Cloud deployment is a design, not a deployment.** No cloud account was used. The
  design document maps every recommendation back to these measurements.

## How to run

```powershell
python cluster.py start rs
python replication_demo.py > results\replication_demo_output.txt      # about 3 minutes
python cluster.py stop rs

python cluster.py start shard
python sharding_demo.py > results\sharding_demo_output.txt            # about 1 minute
python cluster.py stop shard
```

The demo data lives in `cluster/data` (about 1.5 GB). Delete that folder after review to
free disk space. The scripts recreate it when run again. It should not be committed to
Git: add `portfolio-5-distributed-cloud/cluster/data/` to `.gitignore`.
