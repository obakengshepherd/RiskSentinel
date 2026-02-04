"""
RiskSentinel — Health-Check API
GET  /api/v1/health   →  { status, db, kafka, uptime_seconds, version }

Used by Kubernetes liveness / readiness probes.
"""

import time
import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import HealthCheck
from app.services.db import get_db

logger = logging.getLogger("risksentinel.api.health")
router = APIRouter()

# Record the time the process started (module-level, set once)
_PROCESS_START = time.perf_counter()
APP_VERSION = "1.0.0"


@router.get(
    "/",
    response_model=HealthCheck,
    summary="Health Check",
    description="Liveness / readiness probe.  Verifies DB and Kafka connectivity.",
)
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    db_status = "healthy"
    kafka_status = "healthy"
    overall = "healthy"

    # ── DB ping ────────────────────────────────────────────────────────────
    try:
        result = await db.execute(text("SELECT 1"))
        assert result.scalar() == 1
    except Exception as exc:
        db_status = "unhealthy"
        overall = "unhealthy"
        logger.error("DB health-check failed: %s", exc)

    # ── Kafka ping ─────────────────────────────────────────────────────────
    producer = getattr(request.app.state, "kafka_producer", None)
    if producer and producer._producer:
        try:
            # aiokafka exposes a simple check via the internal client
            if not producer._producer._closed:
                kafka_status = "healthy"
            else:
                kafka_status = "unhealthy"
                overall = "degraded"
        except Exception as exc:
            kafka_status = "unhealthy"
            overall = "degraded"
            logger.warning("Kafka health-check failed: %s", exc)
    else:
        kafka_status = "not_configured"
        overall = "degraded" if overall != "unhealthy" else overall

    uptime = round(time.perf_counter() - _PROCESS_START, 2)

    body = HealthCheck(
        status=overall,
        db=db_status,
        kafka=kafka_status,
        uptime_seconds=uptime,
        version=APP_VERSION,
    )

    # Return 503 if unhealthy so that Kubernetes can detect it
    status_code = status.HTTP_200_OK if overall != "unhealthy" else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=body.model_dump(), status_code=status_code)
