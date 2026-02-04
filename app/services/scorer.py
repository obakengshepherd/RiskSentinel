"""
RiskSentinel — Risk Scoring Orchestrator

Pulls together every scoring signal, produces a single composite score,
persists the RiskScore row, fires alerts when thresholds are crossed, and
writes an AuditLog entry — all in one transactional unit.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import (
    Alert, AuditLog, FraudRule, RiskScore, Transaction,
)
from app.rules.engine import evaluate_rules
from app.services.velocity import compute_velocity_score, compute_anomaly_score

logger = logging.getLogger("risksentinel.scorer")


# ===========================================================================
# Level classifier
# ===========================================================================
def _classify_risk(score: float) -> str:
    if score >= settings.RISK_SCORE_CRITICAL:
        return "CRITICAL"
    if score >= settings.RISK_SCORE_HIGH:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"


# ===========================================================================
# ML bridge  (pluggable; returns 0.0 when ML is disabled or model missing)
# ===========================================================================
async def _ml_score(transaction: Transaction) -> Optional[float]:
    if not settings.ML_ENABLED:
        return None
    try:
        from ml.predict import predict_score        # lazy import — optional dep
        return await predict_score(transaction)
    except ImportError:
        logger.debug("ML module not available — skipping ML score.")
        return None
    except Exception as exc:
        logger.warning("ML prediction failed: %s", exc)
        return None


# ===========================================================================
# Core orchestration
# ===========================================================================
async def score_transaction(
    db: AsyncSession,
    transaction: Transaction,
) -> RiskScore:
    """
    1. Fetch active rules from DB                  → rule_score
    2. Velocity check (sliding-window)             → velocity_score
    3. Anomaly check  (z-score on amount)          → anomaly_score
    4. Optional ML inference                       → ml_score
    5. Weighted composite                          → composite_score
    6. Persist RiskScore + optional Alert + AuditLog

    Weights (tunable via config / environment):
        rule_score      0.35
        velocity_score  0.25
        anomaly_score   0.25
        ml_score        0.15  (only if available)
    """

    # ── 1. Rules ───────────────────────────────────────────────────────────
    stmt = select(FraudRule).where(FraudRule.is_active.is_(True))
    result = await db.execute(stmt)
    active_rules: List[FraudRule] = list(result.scalars())

    rule_score, triggered_codes, rule_explanation = evaluate_rules(
        transaction, active_rules
    )

    # ── 2. Velocity ────────────────────────────────────────────────────────
    velocity_score, velocity_details = await compute_velocity_score(
        db, transaction.sender_id, transaction.id
    )

    # ── 3. Anomaly ─────────────────────────────────────────────────────────
    anomaly_score, anomaly_details = await compute_anomaly_score(
        db, transaction.sender_id, transaction.amount_zar, transaction.id
    )

    # ── 4. ML ──────────────────────────────────────────────────────────────
    ml_raw: Optional[float] = await _ml_score(transaction)

    # ── 5. Composite ───────────────────────────────────────────────────────
    if ml_raw is not None:
        composite = (
            rule_score      * 0.30 +
            velocity_score  * 0.22 +
            anomaly_score   * 0.23 +
            ml_raw          * 0.25
        )
    else:
        # Redistribute ML weight proportionally across the other three
        composite = (
            rule_score      * 0.35 +
            velocity_score  * 0.33 +
            anomaly_score   * 0.32
        )

    composite = round(min(composite, 1.0), 4)
    risk_level = _classify_risk(composite)

    # ── 6. Persist ─────────────────────────────────────────────────────────
    explanation: Dict = {
        "rules": rule_explanation,
        "velocity": velocity_details,
        "anomaly": anomaly_details,
        "ml_score": ml_raw,
        "weights": {
            "rule": 0.35 if ml_raw is None else 0.30,
            "velocity": 0.33 if ml_raw is None else 0.22,
            "anomaly": 0.32 if ml_raw is None else 0.23,
            "ml": 0.25 if ml_raw is not None else 0.0,
        },
    }

    risk_score_row = RiskScore(
        transaction_id=transaction.id,
        composite_score=composite,
        rule_score=round(rule_score, 4),
        velocity_score=round(velocity_score, 4),
        anomaly_score=round(anomaly_score, 4),
        ml_score=round(ml_raw, 4) if ml_raw is not None else None,
        risk_level=risk_level,
        triggered_rules=triggered_codes,
        explanation=explanation,
    )
    db.add(risk_score_row)

    # Update transaction status
    if risk_level == "CRITICAL":
        transaction.status = "flagged"
    elif risk_level == "HIGH":
        transaction.status = "flagged"

    # ── Alerts (only for HIGH / CRITICAL) ──────────────────────────────────
    if risk_level in ("HIGH", "CRITICAL"):
        alert = Alert(
            transaction_id=transaction.id,
            severity=risk_level,
            alert_type="FRAUD_SUSPECTED" if rule_score > 0.5 else (
                "VELOCITY_BREACH" if velocity_score >= 1.0 else "ANOMALY_DETECTED"
            ),
            message=(
                f"Transaction {transaction.id} scored {composite:.2f} "
                f"[{risk_level}]. Triggered rules: {triggered_codes}."
            ),
        )
        db.add(alert)
        logger.warning("ALERT created: severity=%s txn=%s", risk_level, transaction.id)

    # ── Audit log ──────────────────────────────────────────────────────────
    audit = AuditLog(
        transaction_id=transaction.id,
        actor="system",
        action="TRANSACTION_SCORED",
        details={
            "composite_score": composite,
            "risk_level": risk_level,
            "triggered_rules": triggered_codes,
        },
    )
    db.add(audit)

    await db.flush()   # push to DB within the caller's transaction
    logger.info(
        "Scored txn=%s composite=%.4f level=%s",
        transaction.id, composite, risk_level,
    )
    return risk_score_row
