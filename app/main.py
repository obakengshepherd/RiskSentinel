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
from fastapi.middleware.gzip import GZIPMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import make_asgi_app

from app.api.routes import transactions, alerts, rules, dashboard, health
from app.services.kafka_producer import KafkaProducer
from app.services.db import engine, Base
from app.services.observability import (
    setup_logging,
    Metrics,
    metrics_middleware,
    REQUEST_ID_CTX,
    USER_ID_CTX,
)
from app.services.security import get_current_user
from app.services.errors import exception_to_response, RiskSentinelException
from app.config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger("risksentinel")


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)


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
    if settings.KAFKA_BOOTSTRAP_SERVERS:
        app.state.kafka_producer = KafkaProducer()
        await app.state.kafka_producer.start()
        logger.info("Kafka producer connected.")
    else:
        logger.warning("Kafka disabled — no bootstrap servers configured.")

    yield  # ← application runs here

    # --- shutdown ---
    if hasattr(app.state, "kafka_producer"):
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
    docs_url="/docs" if not settings.is_production() else None,
    redoc_url="/redoc" if not settings.is_production() else None,
    openapi_url="/openapi.json" if not settings.is_production() else None,
)

# ---------------------------------------------------------------------------
# Middleware Stack (order matters)
# ---------------------------------------------------------------------------

# 1. GZIP compression
app.add_middleware(GZIPMiddleware, minimum_size=1000)

# 2. CORS
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=True,
    )

# 3. Trusted hosts
if settings.ALLOWED_HOSTS != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.ALLOWED_HOSTS,
    )

# 4. Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next) -> Response:
    """
    Attach a unique request-id, set context variables, measure latency,
    and record metrics.
    """
    request_id = str(uuid.uuid4())
    REQUEST_ID_CTX.set(request_id)
    request.state.request_id = request_id
    request.state.start_time = time.perf_counter()

    try:
        response: Response = await call_next(request)
    except RiskSentinelException as exc:
        resp, log_level = exception_to_response(exc, request_id=request_id)
        getattr(logger, log_level)("RiskSentinelException: %s", exc)
        return resp
    except RequestValidationError as exc:
        resp, log_level = exception_to_response(exc, request_id=request_id)
        return resp
    except Exception as exc:
        resp, log_level = exception_to_response(exc, request_id=request_id)
        getattr(logger, log_level)("Unhandled exception: %s", exc)
        return resp
    finally:
        elapsed_ms = (time.perf_counter() - request.state.start_time) * 1_000

        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"

        logger.info(
            "HTTP request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": elapsed_ms,
            },
        )

    return response


@app.middleware("http")
async def metrics_collection_middleware(request: Request, call_next) -> Response:
    """Record metrics for requests."""
    if settings.METRICS_ENABLED:
        return await metrics_middleware(request, call_next)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------------------------
def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded."""
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": f"Too many requests. {exc.detail}",
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


@app.exception_handler(RiskSentinelException)
async def risksentinel_exception_handler(
    request: Request,
    exc: RiskSentinelException,
):
    """Handle RiskSentinel domain exceptions."""
    request_id = getattr(request.state, "request_id", None)
    resp, log_level = exception_to_response(exc, request_id=request_id)
    getattr(logger, log_level)("RiskSentinelException: %s", exc)
    return resp


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(health.router, prefix="/api/v1/health", tags=["Health"])
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["Transactions"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
app.include_router(rules.router, prefix="/api/v1/rules", tags=["Rules"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])

# Dynamic auth router import (add after other imports at top)
try:
    from app.api.routes import auth
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
except ImportError:
    logger.warning("Authentication module not available")


# ---------------------------------------------------------------------------
# Prometheus Metrics Endpoint
# ---------------------------------------------------------------------------
if settings.METRICS_ENABLED:
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)


# ---------------------------------------------------------------------------
# Custom OpenAPI schema
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

    # Add security schemes
    openapi_schema["components"] = {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "JWT Bearer token authentication",
            },
            "apiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "API Key authentication (fallback)",
            },
        }
    }

    openapi_schema["servers"] = [
        {"url": "/", "description": "Current environment"},
        {"url": "https://api.risksentinel.io", "description": "Production"},
    ]

    openapi_schema["info"]["x-logo"] = {
        "url": "https://risksentinel.io/logo.png"
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# ---------------------------------------------------------------------------
# Root endpoint
# ---------------------------------------------------------------------------
@app.get("/", tags=["Info"])
async def root():
    """API root — redirects to docs."""
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "environment": settings.APP_ENV,
        "docs": "/docs" if not settings.is_production() else None,
    }

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
