# RiskSentinel
**Real-Time Fraud & Risk Detection Engine — South African Payment Systems**

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue?style=flat-square)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-green?style=flat-square)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue?style=flat-square)](https://postgresql.org)
[![Kafka](https://img.shields.io/badge/Apache%20Kafka-latest-orange?style=flat-square)](https://kafka.apache.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue?style=flat-square)](https://docker.com)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-HPA-blue?style=flat-square)](https://kubernetes.io)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-IsolationForest-purple?style=flat-square)](https://scikit-learn.org)

---

## What RiskSentinel Does

RiskSentinel scores every inbound payment transaction in real time against four independent risk signals and fuses them into a single auditable composite score:

| Signal | Method | Weight |
|---|---|---|
| **Rule Engine** | Dynamic JSON rules (CRUD API) | 35 % |
| **Velocity Check** | 5-min sliding-window count + sum | 33 % |
| **Anomaly Detection** | Per-sender z-score on amount | 32 % |
| **ML Inference** | IsolationForest (pluggable) | replaces above when enabled |

When a score crosses a configured threshold an `Alert` is persisted, published to Kafka, and (optionally) webhook-fanned-out — all inside a single database transaction for consistency.

---

## Architecture at a Glance

```
┌─────────────┐   HTTP POST    ┌────────────────────┐
│  Payment    │ ──────────────►│   FastAPI  /api/v1  │
│  Gateway    │                │                    │
└─────────────┘                │  ┌──────────────┐  │
                               │  │  Scorer      │  │◄── rules  ── DB
                               │  │  orchestrator│  │◄── velocity ── DB
                               │  └──────┬───────┘  │◄── anomaly ── DB
                               │         │ ML?      │◄── ML    ── pkl
                               │         ▼          │
                               │  ┌──────────────┐  │
                               │  │  Alert +     │  │
                               │  │  AuditLog    │  │
                               │  └──────┬───────┘  │
                               └─────────┼──────────┘
                                         │
                        ┌────────────────┼──────────────────┐
                        ▼                ▼                   ▼
                   ┌─────────┐    ┌───────────┐      ┌───────────┐
                   │PostgreSQL│    │  Kafka     │      │ Webhooks  │
                   │ (async)  │    │ (scored +  │      │ (optional)│
                   └─────────┘    │  alert)    │      └───────────┘
                                  └───────────┘
```

---

## Repository Layout

```
RiskSentinel/
├── app/
│   ├── __init__.py
│   ├── main.py              ← FastAPI app, middleware, router registration
│   ├── config.py            ← pydantic-settings (all env-driven tunables)
│   ├── seed.py              ← inserts default fraud rules once
│   ├── api/
│   │   └── routes/
│   │       ├── transactions.py   ← POST/GET transactions + scoring
│   │       ├── alerts.py         ← list / patch alerts
│   │       ├── rules.py          ← CRUD fraud rules
│   │       ├── dashboard.py      ← KPI aggregations
│   │       └── health.py         ← liveness / readiness
│   ├── services/
│   │   ├── db.py            ← async engine, session, init_db
│   │   ├── scorer.py        ← composite scoring orchestrator
│   │   ├── velocity.py      ← sliding-window + z-score anomaly
│   │   ├── kafka_producer.py← producer & consumer wrappers
│   │   └── alerting.py      ← fan-out dispatcher (Kafka + webhook)
│   ├── models/
│   │   ├── models.py        ← SQLAlchemy ORM (5 tables)
│   │   └── schemas.py       ← Pydantic request/response DTOs
│   └── rules/
│       ├── engine.py        ← JSON-rule evaluator (operator registry)
│       └── default_rules.py ← 7 pre-built SA fraud rules
├── ml/
│   ├── predict.py           ← IsolationForest inference bridge
│   └── train.py             ← synthetic-data generator + training script
├── infra/
│   ├── k8s/
│   │   └── deployment.yaml  ← Deployment, Service, HPA, ConfigMap, Secret
│   └── migrations/
│       └── env.py           ← Alembic async env
├── tests/
│   └── test_risksentinel.py ← 30+ unit & integration tests
├── Dockerfile               ← multi-stage production image
├── docker-compose.yml       ← full local stack (PG + Kafka + API + seed)
├── requirements.txt
├── .env.example
└── README.md                ← this file
```

---

## Quick Start (PowerShell)

### 1. Clone & enter the project

```powershell
# From wherever your portfolio repos live:
git clone <your-repo-url> RiskSentinel
cd RiskSentinel
```

### 2. Create directories (if cloned from a tarball / zip without git)

```powershell
# Run these only if the directories do not already exist:
New-Item -ItemType Directory -Path "app\api\routes"  -Force
New-Item -ItemType Directory -Path "app\services"    -Force
New-Item -ItemType Directory -Path "app\models"      -Force
New-Item -ItemType Directory -Path "app\rules"       -Force
New-Item -ItemType Directory -Path "ml\models"       -Force
New-Item -ItemType Directory -Path "infra\k8s"       -Force
New-Item -ItemType Directory -Path "infra\migrations"-Force
New-Item -ItemType Directory -Path "tests"           -Force
```

### 3. Set up the Python virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate
pip install -r requirements.txt
```

### 4. Configure environment

```powershell
# Copy the template; edit secrets as needed
Copy-Item .env.example .env
```

### 5. Train the ML model (one-time)

```powershell
python -m ml.train
# Produces:  ml/models/anomaly_model.pkl
#            ml/models/training_report.json
```

### 6. Spin up the full stack with Docker Compose

```powershell
docker compose up --build
# ── what starts ──────────────────────────────────
# db        → PostgreSQL on :5432
# zookeeper → ZooKeeper  on :2181
# kafka     → Kafka      on :9092
# seed      → inserts default rules (exits after)
# api       → FastAPI    on :8000
```

### 7. Smoke-test the API

```powershell
# Health check
Invoke-RestMethod http://localhost:8000/api/v1/health/

# Submit a transaction (instant scoring)
$body = @{
    sender_id        = "alice_001"
    receiver_id      = "bob_002"
    amount_zar       = 75000
    channel          = "mobile_banking"
    merchant_category= "electronics"
} | ConvertTo-Json

Invoke-RestMethod -Uri http://localhost:8000/api/v1/transactions/ `
                  -Method POST `
                  -ContentType application/json `
                  -Body $body

# List all alerts
Invoke-RestMethod http://localhost:8000/api/v1/alerts/

# Dashboard KPIs
Invoke-RestMethod http://localhost:8000/api/v1/dashboard/summary
```

### 8. Run the test suite

```powershell
pip install pytest pytest-asyncio httpx aiosqlite factory-boy
pytest tests/ -v --tb=short
```

---

## Running WITHOUT Docker (local dev)

```powershell
# 1. Start PostgreSQL & Kafka externally (or via Docker individually)
# 2. Activate venv
.venv\Scripts\Activate

# 3. Seed default rules
python -m app.seed

# 4. Launch with hot-reload
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## API Reference (summary)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/transactions/` | Submit + score a transaction |
| `GET` | `/api/v1/transactions/` | Paginated list (filter by status, sender) |
| `GET` | `/api/v1/transactions/{id}` | Single transaction detail |
| `GET` | `/api/v1/transactions/{id}/score` | Full risk-score breakdown + explanation |
| `GET` | `/api/v1/alerts/` | Paginated alerts (filter severity, status) |
| `PATCH` | `/api/v1/alerts/{id}` | Acknowledge / resolve / assign |
| `POST` | `/api/v1/rules/` | Create a dynamic fraud rule |
| `GET` | `/api/v1/rules/` | List rules (active_only flag) |
| `PUT` | `/api/v1/rules/{id}` | Full replace |
| `PATCH` | `/api/v1/rules/{id}` | Partial update (toggle, weight …) |
| `DELETE` | `/api/v1/rules/{id}` | Soft-deactivate |
| `GET` | `/api/v1/dashboard/summary` | Real-time KPI panel |
| `GET` | `/api/v1/dashboard/risk-trend` | Hourly avg-score (last 24 h) |
| `GET` | `/api/v1/health/` | Liveness / readiness probe |

Full interactive docs: **http://localhost:8000/docs** (Swagger UI)
ReDoc:                  **http://localhost:8000/redoc**

---

## Kubernetes Deployment

```powershell
# 1. Build & push the image
docker build -t your-registry/risksentinel:latest .
docker push  your-registry/risksentinel:latest

# 2. Edit infra/k8s/deployment.yaml → update the image reference

# 3. Apply manifests
kubectl apply -f infra/k8s/deployment.yaml
# Creates: Namespace, ConfigMap, Secret, Deployment (2 replicas), Service, HPA

# 4. Verify
kubectl -n risksentinel get pods
kubectl -n risksentinel get hpa
```

The HPA auto-scales the API pods from 2 → 10 based on CPU (60 %) and memory (75 %).

---

## Configuration Reference

Every value in `config.py` can be overridden via an environment variable or `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `RISK_SCORE_HIGH` | 0.7 | Score threshold for HIGH flag |
| `RISK_SCORE_CRITICAL` | 0.9 | Score threshold for CRITICAL flag + alert |
| `VELOCITY_WINDOW_SECONDS` | 300 | Sliding-window duration |
| `VELOCITY_MAX_TXN_COUNT` | 10 | Max transactions allowed in window |
| `VELOCITY_MAX_TOTAL_ZAR` | 50 000 | Max cumulative ZAR in window |
| `AMOUNT_ANOMALY_ZSCORE` | 3.0 | Z-score threshold for anomaly flag |
| `ML_ENABLED` | true | Toggle ML inference |

---

## Design Decisions

**Auditability** — every scoring run writes a JSON `explanation` block that shows exactly which rules fired, what the velocity window contained, and what z-score was computed. The `AuditLog` table captures actor + action + timestamp for regulatory replay.

**Explainability** — the `/transactions/{id}/score` endpoint returns the full breakdown so an analyst can see *why* a score landed where it did, without touching the database directly.

**Extensibility** — new fraud rules require zero code changes; they are created via the CRUD API and evaluated by the operator-registry engine. New operators (e.g. `regex`, `range`) are a single function + dict entry.

**Resilience** — Kafka decouples ingestion from scoring; if the scorer is temporarily down, messages queue and are replayed. The consumer uses `auto_offset_reset=earliest` so nothing is lost on restart.

**ML is optional** — if `ML_ENABLED=false` or the model file is missing the system continues with the three rule-based signals, redistributing weights automatically.

---

## License

MIT — use, modify, deploy freely.
