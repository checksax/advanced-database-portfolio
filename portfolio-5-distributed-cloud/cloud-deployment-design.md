# Cloud Deployment Design — St. Grace Specialist Hospital

This design scales the local experiments in this folder up to production, for a hospital
with two branches (Lagos and Abuja). Wherever a number below comes from our own
measurements, the source demo is given in brackets, e.g. [R5].

## 1. Requirements

| Requirement | Target | Why |
|---|---|---|
| Availability | 24/7; survive the loss of one server or one data centre without manual action | Clinicians cannot stop treating patients when a server fails |
| Data loss on failure (RPO) | **0** for clinical and financial writes | A lost prescription or payment is a safety or legal incident |
| Recovery time (RTO) | < 1 minute for a server failure; < 4 hours for loss of a whole region | Matches our measured failover (16.5 s [R5]) with margin |
| Consistency | Billing, stock and beds: strong (no anomalies [Portfolio 3]). Chart viewing: may lag by seconds | Different data has different tolerance |
| Data protection | Patient data is sensitive personal data under the Nigeria Data Protection Act 2023: encryption, access control, audit, and lawful basis for any transfer outside Nigeria | Legal duty and patient trust |
| Growth | 5 years of records for two branches, then more branches | Plan for sharding before it is urgent |

## 2. Architecture

```text
             Lagos branch                                    Abuja branch
        (clinics, pharmacy, wards)                       (clinics, pharmacy, wards)
                  │  TLS / private link                           │
                  ▼                                               ▼
     ┌───────────────────────────────── Primary cloud region ─────────────────────────────────┐
     │   Application tier (stateless, autoscaled across 3 availability zones)                   │
     │                                                                                          │
     │   PostgreSQL — SYSTEM OF RECORD (billing, payments, stock, beds, appointments)           │
     │     AZ-1: primary ──sync──► AZ-2: standby (automatic failover)                           │
     │                    └async─► AZ-3: read replica (reports, Portfolio 2 MV refresh)         │
     │                                                                                          │
     │   MongoDB replica set — CLINICAL RECORD (encounters, lab results, vitals time series)    │
     │     AZ-1: primary   AZ-2: secondary   AZ-3: secondary     writes w:"majority"           │
     │     + 1 hidden analytics member (antibiotic resistance, surveillance; never serves app)  │
     │                                                                                          │
     │   Change-data-capture: PostgreSQL ──► MongoDB (clinical document built from SQL rows)    │
     └──────────────────────────────────────────────────────────────────────────────────────────┘
                  │ encrypted backups + async replication
                  ▼
     ┌──────────────── Disaster-recovery region (warm standby) ─────────────────┐
     │ PostgreSQL cross-region replica · MongoDB DR member (priority 0, votes 0) │
     │ Point-in-time backups (35 days) · quarterly restore drill                 │
     └───────────────────────────────────────────────────────────────────────────┘
```

**Why three availability zones?** A replica set needs a majority of votes to elect a
primary. With 3 members in 3 zones, losing any one zone leaves 2 of 3, so the set fails
over automatically [R5]. With 2 zones, losing the zone holding 2 members leaves 1 of 3 and
**no primary**, which is exactly what R6 showed: writes refused, reads only.

**Why is the DR member non-voting?** A member in a distant region would slow elections
and could tip the majority across a slow WAN link. As a priority-0, non-voting member it
receives every write but can never become primary by accident. Promoting it is a
deliberate disaster-recovery step.

## 3. Consistency settings per workload

| Workload | PostgreSQL | MongoDB | Behaviour during a failure |
|---|---|---|---|
| Payments, stock, bed allocation | `synchronous_commit = on` with one synchronous standby | (kept in PostgreSQL) | **CP**: a write is confirmed only when two zones have it; may pause up to a minute during failover, never lost |
| Prescriptions and lab results (clinical writes) | – | `w: "majority"`, `j: true`, retryable writes | **CP**: paused for about 16 s during an election, then retried automatically: 0 lost of 166 [R5] |
| Doctor opening a chart | – | `readConcern: "majority"`, `readPreference: "primary"` | Never sees data that could be rolled back |
| Ward dashboards, surveillance | read replica | `readPreference: "secondaryPreferred"`, `maxStalenessSeconds: 90` | **AP-leaning**: keeps working with seconds of lag, even with no primary [R6] |
| "I just updated this, show me" flows | read from primary | **causal-consistency** sessions | A lagging secondary waits instead of returning stale data [R3] |

PACELC summary: under a partition, financial and clinical writes choose **consistency**
(C). In normal operation, everything except dashboards chooses **consistency over
latency** (EC). Our measurements show that costs only about 2 ms per write (w:majority
5.2 ms vs w:1 3.3 ms [R2]).

## 4. Scaling out (sharding plan)

Sharding is not needed on day one. One replica set comfortably holds the current data:
about 250 MB of encounters, 70 MB compressed [Portfolio 4]. The plan is ready for when
data passes about 1–2 TB or one primary can no longer absorb the write load:

| Collection | Shard key | Reason [evidence] |
|---|---|---|
| `encounters` | `{ patient.patient_id: "hashed" }` | Even spread (49.9 / 50.1 % [S1]); a patient's chart is a single-shard query [S2] |
| `patients` | `{ branch: 1, _id: 1 }` with **zones** LAGOS / ABUJA | Each branch's patients live on infrastructure near that branch [S3]; supports a data-residency policy |
| `vitals` (time series) | `{ meta.admission_id: 1, ts: 1 }` | Groups an admission's readings; avoids the hot-shard problem of a pure time key [S4] |
| Never | a timestamp alone as a range key | All new writes land on one shard [S4: 2000 of 2000] |

Each shard is itself a 3-member replica set. A single server failure then costs an
election, not an outage. With 1-member shards, losing one made half the data unavailable
[S5].

For PostgreSQL, the equivalents are declarative partitioning by date (Portfolio 2 showed
pruning and instant archiving), read replicas, and, if ever needed, a distributed extension
such as Citus with `patient_id` as the distribution column.

## 5. Platform options

| Option | Fit | Trade-offs |
|---|---|---|
| **Managed cloud services** (e.g. Amazon RDS / Aurora for PostgreSQL Multi-AZ + MongoDB Atlas; or the Azure or Google equivalents) | Best operational fit: automated failover, patching, backups, point-in-time recovery, encryption | Ongoing cost. The nearest hyperscaler regions to Nigeria are currently outside the country, which needs a legal basis for transferring health data abroad |
| **Hybrid**: primary in a Lagos colocation data centre, DR in a managed cloud region | Keeps the primary copy in-country; cloud gives off-site DR | The hospital (or a partner) runs the primary cluster: patching, monitoring, on-call |
| **Self-managed VMs in the cloud** | Full control, same topology as this lab | Most operational work. Only sensible with in-house database administrators |

**Recommendation:** start hybrid if in-country residency is required by the hospital's
legal advice, otherwise fully managed. Either way, keep the topology above (3 zones or 3
racks, majority writes, non-voting DR copy) and verify the current regional availability of
each service before procurement.

## 6. Security and operations

- **Encryption:** TLS 1.2+ for every connection, including member-to-member; encryption at
  rest with keys in a managed KMS; MongoDB field-level encryption for HIV status and other
  especially sensitive fields.
- **Access:** least-privilege database roles mirroring Portfolio 1 (doctor, pharmacist,
  cashier…). No shared accounts; the `audit_log` trigger from Portfolio 3 plus the database
  services' own audit logs.
- **Network:** databases only on private subnets, reachable from the application tier,
  never from the internet. The lab cluster likewise bound only to 127.0.0.1.
- **Backups:** continuous point-in-time recovery plus daily snapshots copied to the DR
  region; **restore drills every quarter** (an untested backup is not a backup).
- **Monitoring and alerts:** replication lag (alert above 10 s; R3 showed how lag appears),
  oplog window (alert if under 24 h), election events, disk and cache usage, slow queries
  (the Portfolio 2 method applied continuously).
- **Change management:** rolling upgrades member by member (secondaries first, then step
  the primary down). The failover in R5 is exactly what a planned primary restart looks
  like to the application.

## 7. Mapping the lab to production

| Lab (this portfolio) | Production |
|---|---|
| 3 `mongod` processes on one laptop, 127.0.0.1:27018–27020 | 3 servers in 3 availability zones |
| `enableTestCommands` fail point to simulate lag | Real network latency; monitored, never simulated |
| 1-member shards (to fit in 8 GB RAM) | 3-member replica set per shard |
| Election measured at 16.5 s with default `electionTimeoutMillis` 10 s | Same defaults; lower only if the network is reliably fast (risk: false elections) |
| PostgreSQL single instance | Primary + synchronous standby + read replica |
