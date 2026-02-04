"""
RiskSentinel — Alerts API
GET    /api/v1/alerts                 → paginated list with severity / status filters
GET    /api/v1/alerts/{alert_id}      → single alert detail
PATCH  /api/v1/alerts/{alert_id}      → update status / assign analyst
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Alert
from app.models.schemas import AlertListResponse, AlertResponse, AlertUpdate
from app.services.db import get_db

logger = logging.getLogger("risksentinel.api.alerts")
router = APIRouter()

VALID_STATUSES = {"open", "acknowledged", "resolved", "closed"}


# ===========================================================================
# GET  /api/v1/alerts
# ===========================================================================
@router.get(
    "/",
    response_model=AlertListResponse,
    summary="List Alerts",
    description="Filter by severity and/or status.  Default returns only open alerts.",
)
async def list_alerts(
    page: int = 1,
    page_size: int = 25,
    severity: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if severity:
        conditions.append(Alert.severity == severity.upper())
    if status_filter:
        conditions.append(Alert.status == status_filter.lower())
    else:
        conditions.append(Alert.status == "open")   # default: open only

    # count
    count_result = await db.execute(
        select(func.count()).select_from(
            select(Alert).where(and_(*conditions)).subquery()
        )
    )
    total: int = count_result.scalar()  # type: ignore[assignment]

    # paginate
    stmt = (
        select(Alert)
        .where(and_(*conditions))
        .order_by(Alert.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    alerts = list(result.scalars())

    return AlertListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[AlertResponse.model_validate(a) for a in alerts],
    )


# ===========================================================================
# GET  /api/v1/alerts/{alert_id}
# ===========================================================================
@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Get Alert Detail",
)
async def get_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Alert).where(Alert.id == alert_id)
    result = await db.execute(stmt)
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    return AlertResponse.model_validate(alert)


# ===========================================================================
# PATCH /api/v1/alerts/{alert_id}
# ===========================================================================
@router.patch(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Update Alert Status / Assignment",
)
async def update_alert(
    alert_id: str,
    body: AlertUpdate,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Alert).where(Alert.id == alert_id)
    result = await db.execute(stmt)
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")

    if body.status is not None:
        if body.status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Allowed: {sorted(VALID_STATUSES)}",
            )
        alert.status = body.status
        if body.status == "resolved":
            alert.resolved_at = datetime.now(timezone.utc)

    if body.assigned_to is not None:
        alert.assigned_to = body.assigned_to

    await db.commit()
    await db.refresh(alert)
    logger.info("Alert %s updated → status=%s assigned_to=%s", alert_id, alert.status, alert.assigned_to)
    return AlertResponse.model_validate(alert)
