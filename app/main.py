"""
RiskSentinel — Main Application Entry Point
Real-Time Fraud & Risk Detection Engine
"""

import time
import uuid
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.routes import transactions, alerts, rules, dashboard, health
from app.services.kafka_producer import KafkaProducer
from app.services.db import engine, Base
from app.config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("risksentinel")


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap heavy resources once; tear down on shutdown."""
    logger.info("RiskSentinel — initialising …")

    # 1. Create all DB tables (idempotent)
    from app.services.db import init_db
    await init_db()

    # 2. Warm Kafka producer connection
    app.state.kafka_producer = KafkaProducer()
    await app.state.kafka_producer.start()
    logger.info("Kafka producer connected.")

    yield  # ← application runs here

    # --- shutdown ---
    await app.state.kafka_producer.stop()
    logger.info("RiskSentinel — shut down complete.")


# ---------------------------------------------------------------------------
# FastAPI instance
# ---------------------------------------------------------------------------
app = FastAPI(
    title="RiskSentinel",
    description=(
        "Real-Time Fraud & Risk Detection Engine for South African payment systems. "
        "Rule-based scoring, velocity checks, anomaly detection, and live alerting."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS,
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next) -> Response:
    """Attach a unique request-id and measure latency for every inbound call."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.start_time = time.perf_counter()

    response: Response = await call_next(request)

    elapsed_ms = (time.perf_counter() - request.state.start_time) * 1_000
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"

    logger.info(
        "req_id=%s method=%s path=%s status=%d latency_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["Transactions"])
app.include_router(alerts.router,       prefix="/api/v1/alerts",       tags=["Alerts"])
app.include_router(rules.router,        prefix="/api/v1/rules",        tags=["Rules"])
app.include_router(dashboard.router,    prefix="/api/v1/dashboard",    tags=["Dashboard"])
app.include_router(health.router,       prefix="/api/v1/health",       tags=["Health"])


# ---------------------------------------------------------------------------
# Custom OpenAPI schema (logo + server list)
# ---------------------------------------------------------------------------
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    openapi_schema["info"]["x-logo"] = {
        "url": "https://risksentinel.io/logo.png"
    }
    openapi_schema["servers"] = [
        {"url": "/",                          "description": "Local / Docker"},
        {"url": "https://api.risksentinel.io", "description": "Production"},
    ]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
