"""
RiskSentinel — Velocity & Anomaly Detection Service

Velocity
--------
Counts and sums transactions for the *sender* over a configurable sliding window.
If count or total ZAR breaches the configured limit the velocity score spikes.

Anomaly (z-score)
-----------------
Compares the current transaction amount against the sender's historical mean /
std-dev.  A z-score above the configured threshold pushes the anomaly score up.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import Transaction

logger = logging.getLogger("risksentinel.velocity")


# ===========================================================================
# Velocity
# ===========================================================================
async def compute_velocity_score(
    db: AsyncSession,
    sender_id: str,
    current_txn_id: str,
) -> Tuple[float, dict]:
    """
    Query the last N seconds of transactions for *sender_id* (excluding the
    current one that is already being scored).

    Returns
    -------
    velocity_score : float   – 0.0 … 1.0
    details        : dict    – human-readable breakdown for the audit trail
    """
    window_start = datetime.now(timezone.utc) - timedelta(
        seconds=settings.VELOCITY_WINDOW_SECONDS
    )

    # --- count & sum in one query -------------------------------------------
    stmt = (
        select(
            func.count(Transaction.id).label("txn_count"),
            func.coalesce(func.sum(Transaction.amount_zar), 0.0).label("txn_sum"),
        )
        .where(
            Transaction.sender_id == sender_id,
            Transaction.id != current_txn_id,
            Transaction.created_at >= window_start,
        )
    )
    result = await db.execute(stmt)
    row = result.one()
    txn_count: int = row.txn_count
    txn_sum: float = row.txn_sum

    # --- scoring ------------------------------------------------------------
    count_ratio = min(txn_count / settings.VELOCITY_MAX_TXN_COUNT, 1.0)
    amount_ratio = min(txn_sum / settings.VELOCITY_MAX_TOTAL_ZAR, 1.0)

    # Weighted blend: 40 % count, 60 % amount
    velocity_score = round(0.4 * count_ratio + 0.6 * amount_ratio, 4)

    details = {
        "window_seconds": settings.VELOCITY_WINDOW_SECONDS,
        "txn_count_in_window": txn_count,
        "max_txn_count": settings.VELOCITY_MAX_TXN_COUNT,
        "txn_sum_zar": round(txn_sum, 2),
        "max_sum_zar": settings.VELOCITY_MAX_TOTAL_ZAR,
        "count_ratio": round(count_ratio, 4),
        "amount_ratio": round(amount_ratio, 4),
        "breached": velocity_score >= 1.0,
    }

    if details["breached"]:
        logger.warning(
            "VELOCITY BREACH sender=%s count=%d sum=%.2f",
            sender_id, txn_count, txn_sum,
        )

    return velocity_score, details


# ===========================================================================
# Anomaly  (z-score on amount)
# ===========================================================================
async def compute_anomaly_score(
    db: AsyncSession,
    sender_id: str,
    current_amount: float,
    current_txn_id: str,
    lookback_days: int = 30,
) -> Tuple[float, dict]:
    """
    Compute the z-score of *current_amount* relative to the sender's
    historical transaction amounts over the last *lookback_days*.

    Returns
    -------
    anomaly_score : float  – 0.0 … 1.0   (clamped)
    details       : dict
    """
    lookback_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    stmt = (
        select(
            func.avg(Transaction.amount_zar).label("mean"),
            func.stddev_pop(Transaction.amount_zar).label("stddev"),
            func.count(Transaction.id).label("sample_size"),
        )
        .where(
            Transaction.sender_id == sender_id,
            Transaction.id != current_txn_id,
            Transaction.created_at >= lookback_start,
        )
    )
    result = await db.execute(stmt)
    row = result.one()

    mean = row.mean
    stddev = row.stddev
    sample_size = row.sample_size

    # Not enough history → neutral score
    if sample_size < 3 or mean is None or stddev is None or stddev == 0:
        return 0.0, {
            "z_score": None,
            "mean": mean,
            "stddev": stddev,
            "sample_size": sample_size,
            "note": "Insufficient history for anomaly detection",
        }

    z_score = abs(current_amount - mean) / stddev

    # Map z_score to 0–1.  Anything at or above the threshold is 1.0.
    threshold = settings.AMOUNT_ANOMALY_ZSCORE
    anomaly_score = round(min(z_score / threshold, 1.0), 4)

    details = {
        "z_score": round(z_score, 4),
        "mean_zar": round(mean, 2),
        "stddev_zar": round(stddev, 2),
        "sample_size": sample_size,
        "threshold_zscore": threshold,
        "is_anomaly": z_score >= threshold,
    }

    if details["is_anomaly"]:
        logger.warning(
            "ANOMALY DETECTED sender=%s amount=%.2f z_score=%.2f",
            sender_id, current_amount, z_score,
        )

    return anomaly_score, details
