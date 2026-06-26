# SmartHealth Platform — System Design

## System Overview

Three operational loops run at different cadences and drive all platform behaviour.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SMARTHEALTH PLATFORM                         │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  LOOP 1 — Field Data Collection  (minutes → hours)           │  │
│  │                                                              │  │
│  │  Field Worker (PWA)  ──►  WhatsApp Bot  ──►  FastAPI         │  │
│  │       │  offline queue                        │              │  │
│  │       └──────────── sync on reconnect ────────┘              │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼  TimescaleDB + Redis                 │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  LOOP 2 — Prediction & Optimisation  (nightly batch)         │  │
│  │                                                              │  │
│  │  Celery Beat  ──►  XGBoost stockout model                    │  │
│  │                ──►  OR-Tools CP-SAT scheduler                │  │
│  │                ──►  Gemini Flash risk summaries              │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼  push via WebSocket                  │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  LOOP 3 — District Dashboard  (real-time)                    │  │
│  │                                                              │  │
│  │  React Dashboard  ◄──  FastAPI WebSocket  ◄──  Redis pub/sub │  │
│  │  (alerts, maps, KPIs, stock heatmaps)                        │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Technology Decisions

### TimescaleDB over plain PostgreSQL
Vital-sign readings and stock-level snapshots are append-only time-series
data. TimescaleDB hypertables give automatic partitioning by time, continuous
aggregates (pre-computed rollups with no extra ETL), and native compression
that reduces storage ~90 % on cold data. The SQL interface is identical to
PostgreSQL, so the ORM layer (SQLAlchemy + asyncpg) needs no changes.

### OR-Tools CP-SAT over LP solvers
Staff scheduling has hard integer constraints: a nurse cannot be split across
two centres, shift lengths are whole hours, and some workers are part-time.
LP relaxations produce fractional solutions that require rounding, which can
violate constraints or be suboptimal. CP-SAT (Constraint Programming +
Boolean Satisfiability) handles integer domains natively, finds proven-optimal
solutions for district-sized instances (< 500 variables) in under 10 seconds,
and supports soft constraints (preferences) via a weighted objective.

### Gemini Flash over GPT-4
- Gemini Flash processes a 2 000-token district summary in ~1 s at < $0.001,
  making nightly per-district summaries affordable at scale.
- The Flash API is available within Google Cloud regions that satisfy NIC data-
  residency requirements for India.
- GPT-4 adds latency and cost without measurable quality improvement for
  structured-data summarisation tasks (benchmarked internally on 50 districts).

## Stockout Prediction Pipeline

```
1. Celery Beat triggers `run_stockout_predictions` task (02:00 IST nightly)
2. Task queries TimescaleDB:
     SELECT facility_id, drug_id,
            time_bucket('1 day', recorded_at) AS day,
            avg(stock_level) AS avg_stock
     FROM stock_readings
     WHERE recorded_at > now() - INTERVAL '90 days'
     GROUP BY 1, 2, 3
3. Feature engineering (consumption velocity, days-of-stock, seasonal index,
   procurement lead-time from facility metadata)
4. XGBoost model loaded from /app/ml-models/artefacts/stockout_v*.pkl
5. Predictions written to `ml_predictions` table with confidence scores
6. Facilities with p(stockout in 7 days) > 0.7 generate Alert records
7. Redis pub/sub broadcasts alert IDs → WebSocket handler → dashboard clients
```

Model retraining runs weekly via a separate Celery task; new artefacts are
written to the shared PVC (`ml-artefacts-pvc`) and the active version pointer
updated atomically so inference workers see the new model on next task pick-up.

## Offline Sync Protocol

Field workers operate in areas with intermittent 2G/3G connectivity. The PWA
(Vite + React) uses IndexedDB as a local write-ahead log.

**Pull-then-push on reconnect:**
```
1. PULL  GET /sync/pull?since=<last_sync_ts>
         Server returns delta of reference data (drug lists, facility config,
         scheduled visits) since the worker's last successful sync.
2. PUSH  POST /sync/push  { records: [...] }
         Worker uploads all locally queued observations in a single request.
         Server processes records in timestamp order within each facility.
```

**Conflict resolution rules (last-writer-wins with exceptions):**
- Vital signs and stock counts: server timestamp wins if server record is
  newer; otherwise field record is accepted.
- Immunisation administered: field record always wins (idempotent — duplicate
  administration records are deduplicated by `(patient_id, vaccine_id, date)`).
- Scheduled visits marked complete offline: merged additively; a visit cannot
  be "un-completed" by a later server state.

Sync payloads are compressed with gzip. Conflicts are logged to `sync_conflicts`
for supervisor review but do not block the sync from completing.

## DPDP Compliance (Digital Personal Data Protection Act 2023)

| PII Category | Table | Retention | Access Control |
|---|---|---|---|
| Patient name, DoB, Aadhaar last-4 | `patients` | Active + 7 years | Role = doctor, asha, supervisor |
| GPS coordinates of home visits | `field_visits` | 2 years, then nulled | Role = supervisor+ |
| WhatsApp phone number | `patients.phone` | Active only; deleted on opt-out | Role = system (masked in API) |
| Biometric vitals | `vital_readings` | 5 years | Role = doctor, asha |
| ML prediction scores | `ml_predictions` | 1 year | Role = supervisor+ |

Key controls:
- Phone numbers are stored AES-256 encrypted (application-layer); the
  decryption key is held in Kubernetes Secret `smarthealth-secrets`, not in
  the database.
- All API responses mask Aadhaar to last-4 digits; full number is never stored.
- Data processing purpose is recorded per consent record in `patient_consents`.
- Deletion requests trigger a Celery task that anonymises the patient row and
  cascades to related tables within 72 hours per DPDP Section 12(3).

## Scalability Approach

The deployment is sharded at the district level. Each district (n = 750 in
India) is a logical tenant identified by `district_id` on every table.

TimescaleDB hypertables partition first by `district_id` (space partitioning)
then by time. This ensures:
- All queries for one district are served from a single chunk set — no
  cross-shard scatter.
- Adding new districts requires no schema changes; a new `district_id` value
  is inserted into the reference table and data flows naturally.
- A single PostgreSQL instance handles up to ~50 districts comfortably on
  4 vCPU / 16 GB. Horizontal scale (read replicas, additional primary shards)
  can be introduced by routing district ranges to separate connection strings
  via PgBouncer without changing application code.

Kubernetes HPA (defined in `api/deployment.yaml`) autoscales API pods 3 → 20
on CPU > 70 % or memory > 80 %, covering peak load during morning clinic hours
across multiple districts simultaneously.
