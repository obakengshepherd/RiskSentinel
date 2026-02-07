"""
RiskSentinel — Alerts API

GET    /api/v1/alerts                 → paginated list with severity / status filters
GET    /api/v1/alerts/{alert_id}      → single alert detail
PATCH  /api/v1/alerts/{alert_id}      → update status / assign analyst
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Alert, AuditLog
from app.models.schemas import AlertListResponse, AlertResponse, AlertUpdate
from app.services.db import get_db
from app.services.security import get_current_user

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
    description="Filter by severity and/or status. Default returns only open alerts.",
)
async def list_alerts(
    page: int = 1,
    page_size: int = 25,
    severity: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List alerts with optional filtering."""
    conditions = []

    if severity:
        try:
            conditions.append(Alert.severity == severity.upper())
        except Exception as exc:
            logger.warning("Invalid severity filter: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid severity filter",
            ) from exc

    if status_filter:
        if status_filter.lower() not in VALID_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Allowed: {sorted(VALID_STATUSES)}",
            )
        conditions.append(Alert.status == status_filter.lower())
    else:
        conditions.append(Alert.status == "open")  # default: open only

    try:
        # Count
        count_stmt = select(func.count()).select_from(
            select(Alert).where(and_(*conditions) if conditions else True).subquery()
        )
        count_result = await db.execute(count_stmt)
        total: int = count_result.scalar() or 0

        # Paginate
        stmt = (
            select(Alert)
            .where(and_(*conditions) if conditions else True)
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

    except Exception as exc:
        logger.error("Error listing alerts: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve alerts",
        ) from exc


# ===========================================================================
# GET  /api/v1/alerts/{alert_id}
# ===========================================================================
@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Get Alert Detail",
    description="Retrieve full details of a specific alert.",
)
async def get_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get alert details."""
    try:
        stmt = select(Alert).where(Alert.id == alert_id)
        result = await db.execute(stmt)
        alert = result.scalar_one_or_none()

        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alert {alert_id} not found",
            )

        return AlertResponse.model_validate(alert)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error retrieving alert %s: %s", alert_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve alert",
        ) from exc


# ===========================================================================
# PATCH /api/v1/alerts/{alert_id}
# ===========================================================================
@router.patch(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Update Alert",
    description="Update alert status and/or assignment.",
)
async def update_alert(
    alert_id: str,
    body: AlertUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Update alert status and assignment."""
    req_id = getattr(request.state, "request_id", None)

    try:
        stmt = select(Alert).where(Alert.id == alert_id)
        result = await db.execute(stmt)
        alert = result.scalar_one_or_none()

        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alert {alert_id} not found",
            )

        changes = {}

        # Validate and update status
        if body.status is not None:
            if body.status not in VALID_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status. Allowed: {sorted(VALID_STATUSES)}",
                )
            if body.status != alert.status:
                changes["status"] = body.status
                alert.status = body.status

                # Set resolved timestamp if resolving
                if body.status == "resolved":
                    alert.resolved_at = datetime.now(timezone.utc)

        # Update assignment
        if body.assigned_to is not None:
            if body.assigned_to != alert.assigned_to:
                changes["assigned_to"] = body.assigned_to
                alert.assigned_to = body.assigned_to

        if changes:
            # Record audit log
            db.add(AuditLog(
                transaction_id=alert.transaction_id,
                actor=f"analyst:{current_user.get('sub', 'unknown')}",
                action="ALERT_UPDATED",
                details={
                    "alert_id": alert_id,
                    **changes,
                },
            ))

        await db.commit()
        await db.refresh(alert)

        logger.info(
            "Alert %s updated",
            alert_id,
            extra={
                "request_id": req_id,
                "changes": changes,
                "new_status": alert.status,
            },
        )

        return AlertResponse.model_validate(alert)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Error updating alert %s: %s",
            alert_id,
            exc,
            exc_info=True,
            extra={"request_id": req_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update alert",
        ) from exc

