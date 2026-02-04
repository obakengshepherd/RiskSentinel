"""
RiskSentinel — Dashboard API
GET  /api/v1/dashboard/summary     → real-time KPIs for the analyst panel
GET  /api/v1/dashboard/risk-trend  → hourly risk-score averages (last 24 h)
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Alert, RiskScore, Transaction
from app.models.schemas import DashboardSummary, TransactionResponse
from app.services.db import get_db

logger = logging.getLogger("risksentinel.api.dashboard")
router = APIRouter()


# ===========================================================================
# GET  /api/v1/dashboard/summary
# ===========================================================================
@router.get(
    "/summary",
    response_model=DashboardSummary,
    summary="Dashboard KPI Summary",
    description=(
        "Returns aggregated metrics: transaction counts, open / critical "
        "alerts, average risk score, top-5 riskiest transactions, alert "
        "distribution by severity, and velocity breaches in the last hour."
    ),
)
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
    # ── total transactions ─────────────────────────────────────────────────
    total_txn_result = await db.execute(select(func.count()).select_from(Transaction))
    total_transactions: int = total_txn_result.scalar()  # type: ignore[assignment]

    # ── open alerts ────────────────────────────────────────────────────────
    open_alerts_result = await db.execute(
        select(func.count()).select_from(
            select(Alert).where(Alert.status == "open").subquery()
        )
    )
    total_alerts_open: int = open_alerts_result.scalar()  # type: ignore[assignment]

    # ── critical open alerts ───────────────────────────────────────────────
    critical_alerts_result = await db.execute(
        select(func.count()).select_from(
            select(Alert).where(
                and_(Alert.status == "open", Alert.severity == "CRITICAL")
            ).subquery()
        )
    )
    total_alerts_critical: int = critical_alerts_result.scalar()  # type: ignore[assignment]

    # ── average risk score (all time) ──────────────────────────────────────
    avg_result = await db.execute(
        select(func.coalesce(func.avg(RiskScore.composite_score), 0.0))
    )
    avg_risk_score: float = round(avg_result.scalar(), 4)  # type: ignore[arg-type]

    # ── top-5 riskiest transactions ────────────────────────────────────────
    top_stmt = (
        select(Transaction)
        .join(RiskScore, RiskScore.transaction_id == Transaction.id)
        .order_by(RiskScore.composite_score.desc())
        .limit(5)
    )
    top_result = await db.execute(top_stmt)
    top_transactions = list(top_result.scalars())

    top_risk_transactions = [
        TransactionResponse(
            id=t.id,
            external_id=t.external_id,
            sender_id=t.sender_id,
            receiver_id=t.receiver_id,
            amount_zar=t.amount_zar,
            currency=t.currency,
            channel=t.channel,
            status=t.status,
            risk_level=t.risk_score.risk_level if t.risk_score else None,
            composite_score=t.risk_score.composite_score if t.risk_score else None,
            created_at=t.created_at,
        )
        for t in top_transactions
    ]

    # ── alert distribution (open) by severity ─────────────────────────────
    dist_stmt = (
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.status == "open")
        .group_by(Alert.severity)
    )
    dist_result = await db.execute(dist_stmt)
    alert_distribution = {row[0]: row[1] for row in dist_result.all()}

    # ── velocity breaches in last hour ─────────────────────────────────────
    # Proxy: count of HIGH/CRITICAL alerts created in the last 60 minutes
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    vel_result = await db.execute(
        select(func.count()).select_from(
            select(Alert).where(
                and_(
                    Alert.severity.in_(["HIGH", "CRITICAL"]),
                    Alert.alert_type == "VELOCITY_BREACH",
                    Alert.created_at >= one_hour_ago,
                )
            ).subquery()
        )
    )
    velocity_breaches_last_hour: int = vel_result.scalar()  # type: ignore[assignment]

    return DashboardSummary(
        total_transactions=total_transactions,
        total_alerts_open=total_alerts_open,
        total_alerts_critical=total_alerts_critical,
        avg_risk_score=avg_risk_score,
        top_risk_transactions=top_risk_transactions,
        alert_distribution=alert_distribution,
        velocity_breaches_last_hour=velocity_breaches_last_hour,
    )


# ===========================================================================
# GET  /api/v1/dashboard/risk-trend
# ===========================================================================
@router.get(
    "/risk-trend",
    response_model=list[dict],
    summary="Hourly Risk-Score Trend (last 24 h)",
)
async def risk_trend(db: AsyncSession = Depends(get_db)):
    """
    Returns a list of { hour, avg_score, txn_count } buckets.
    Uses PostgreSQL date_trunc for server-side grouping.
    """
    from sqlalchemy import text

    stmt = text("""
        SELECT
            date_trunc('hour', rs.scored_at AT TIME ZONE 'UTC') AS hour,
            round(avg(rs.composite_score)::numeric, 4)          AS avg_score,
            count(*)                                             AS txn_count
        FROM risk_scores rs
        WHERE rs.scored_at >= now() AT TIME ZONE 'UTC' - interval '24 hours'
        GROUP BY hour
        ORDER BY hour ASC
    """)
    result = await db.execute(stmt)
    rows = result.fetchall()

    return [
        {
            "hour": row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]),
            "avg_score": float(row[1]),
            "txn_count": int(row[2]),
        }
        for row in rows
    ]
