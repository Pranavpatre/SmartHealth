# SmartHealth — AI-Powered District Health Operating System

> Predict operational failures at PHCs and CHCs before patients are affected.
> Close the intervention loop in under 5 minutes.

---

## Problem

PHCs and CHCs track medicine stock, patient footfall, bed availability, doctor attendance,
and diagnostic supplies on paper. District administrators discover failures 2–5 days after
they occur — after patients are already affected.

## Solution

A predictive control tower that:
- Forecasts stockouts 48–72 hours ahead with confidence scores and reasoning
- Predicts patient demand (footfall, staffing needs) using weather, disease, and calendar signals
- Recommends inter-facility resource redistribution before shortages occur
- Scores every facility 0–100 and surfaces the bottom-5 for district officer intervention
- Closes the loop via WhatsApp one-tap approve/defer — no dashboard login required
- Works offline and in 8+ regional languages with voice input

## Judging Criteria Alignment

| Criterion | How We Address It |
|----------------------------|----------------------------------------------------------------|
| Problem-solution fit | Replaces 5-day manual reporting cycle with 2-hour AI alerts |
| AI/technical execution | 7 AI modules: forecasting, optimization, anomaly, LLM chat |
| Deployability & scalability| Offline-first, 2G-ready, NIC Cloud compatible, Docker/K8s |
| Inclusivity & accessibility| 8 regional languages, voice input, WhatsApp & SMS channels |
| Impact potential | 60% fewer stockouts, 95% faster alerts, 10→10,000 PHC scaling |
| Presentation & clarity | Live 3-min demo loop: alert → approve → resolve → score update |

---

## Project Structure

```
SmartHealth/
├── backend/
│   ├── api/                 FastAPI REST + WebSocket endpoints
│   ├── ml-models/
│   │   ├── stockout/        Prophet + XGBoost time-series forecasting
│   │   ├── footfall/        LightGBM demand prediction
│   │   ├── redistribution/  OR-Tools optimization solver
│   │   ├── health-score/    Composite facility scoring
│   │   ├── anomaly/         Isolation Forest anomaly detection
│   │   └── diagnostics/     Test/reagent availability forecasting
│   ├── data-pipeline/       Ingestion, ETL, offline sync, retraining
│   └── integrations/        e-Aushadhi, HMIS, ABDM, WhatsApp API
├── frontend/
│   ├── dashboard/           District admin React app (i18n, maps)
│   └── field-app/           PHC worker PWA (offline-first, voice)
├── infrastructure/
│   ├── docker/              Dockerfiles and compose files
│   └── k8s/                 Kubernetes manifests for production
├── data/
│   ├── schemas/             PostgreSQL + TimescaleDB migrations
│   └── sample/              Synthetic demo data (10 PHCs, 90 days)
├── docs/
│   ├── architecture/        System design diagrams
│   ├── requirements/        Feature specs and user stories
│   └── presentation/        Demo script and pitch assets
└── scripts/
    └── seed.py              Cold-start data bootstrapper
```

---

## Tech Stack

| Layer | Technology |
|------------|-------------------------------------------------|
| Backend | FastAPI (Python 3.11) |
| Database | PostgreSQL 16 + TimescaleDB + PostGIS |
| Cache | Redis 7 |
| ML | Prophet, XGBoost, LightGBM, OR-Tools, sklearn |
| LLM | Gemini 2.0 Flash (multilingual) / Llama 3.1 |
| Frontend | React 18 + Tailwind + i18next |
| Field App | React PWA (offline-first, IndexedDB sync) |
| Messaging | Meta WhatsApp Cloud API + Twilio SMS |
| Infra | Docker + Kubernetes (NIC Cloud compatible) |

---

## Quick Start

```bash
# 1. Start infrastructure
docker compose up -d

# 2. Run migrations
psql -U smarthealth smarthealth < data/schemas/001_core.sql
psql -U smarthealth smarthealth < data/schemas/002_seed_demo.sql

# 3. Start backend
cd backend/api && pip install -r requirements.txt && uvicorn main:app --reload

# 4. Start dashboard
cd frontend/dashboard && npm install && npm run dev
```

---

## AI Modules

| Module | Model | Output |
|--------|-------|--------|
| Stockout Prediction | Prophet + XGBoost | Days until stockout, confidence, recommendation |
| Diagnostics Shortage | Prophet + XGBoost | Reagent/kit runout forecast |
| Footfall Forecast | LightGBM | Expected patients + staffing suggestion |
| Redistribution | OR-Tools LP | Optimal transfer plan with cost savings |
| Facility Health Score | Weighted composite | 0-100 score + status |
| Anomaly Detection | Isolation Forest | Outbreak / data anomaly flags |
| LLM Assistant | Gemini 2.0 Flash | Multilingual NL queries on live data |

---

## Government Alignment

- **e-Aushadhi**: sync existing drug inventory (augments, does not replace)
- **HMIS/NHM**: pull facility metadata to avoid duplicate entry burden
- **ABDM**: facility registry alignment for PHC/CHC identification
- **DPDP Act 2023**: no individual PII stored, aggregated counts only, full audit log
- **NIC Cloud**: Docker images deployable on MeitY-approved infrastructure

---

## Demo Scenario (3 Minutes)

1. Open district dashboard → PHC-12 shows RED
2. Click → AI explains: "Insulin out in 2 days. Monsoon +20%, dengue trend +15%"
3. System suggests: "Transfer 60 units from PHC-8. 4km. Saves ₹18,000."
4. Admin taps Approve → PHC-8 worker gets WhatsApp in Hindi
5. PHC-12 health score updates live: RED → YELLOW
6. Voice demo: worker says "आज 180 मरीज आए" → dashboard updates
