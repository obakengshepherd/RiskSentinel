"""
RiskSentinel — Transactions API

POST /api/v1/transactions            → ingest + score in one call
GET  /api/v1/transactions            → paginated list with filters
GET  /api/v1/transactions/{txn_id}   → single transaction + score + audit
"""

import logging
import time
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog, Transaction, RiskScore
from app.models.schemas import (
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
    RiskScoreResponse,
)
from app.services.db import get_db
from app.services.scorer import score_transaction
from app.services.alerting import dispatch_alert
from app.services.security import get_current_user
from app.services.errors import TransactionError, ScoringError
from app.services.observability import (
    log_transaction_scored,
    Metrics,
    set_user_id,
)
from app.config import settings

logger = logging.getLogger("risksentinel.api.transactions")
router = APIRouter()


# ===========================================================================
# POST  /api/v1/transactions
# ===========================================================================
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=TransactionResponse,
    summary="Submit & Score a Transaction",
    description=(
        "Accepts a raw payment transaction, persists it, runs the full "
        "risk-scoring pipeline (rules → velocity → anomaly → ML), creates "
        "alerts if thresholds are breached, and returns the enriched result."
    ),
)
async def create_transaction(
    payload: TransactionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Submit a transaction for risk scoring."""
    req_id = getattr(request.state, "request_id", None)
    set_user_id(current_user.get("sub", "unknown"))
    start_time = time.perf_counter()

    try:
        # ── persist ────────────────────────────────────────────────────────
        txn = Transaction(
            external_id=payload.external_id,
            sender_id=payload.sender_id,
            receiver_id=payload.receiver_id,
            amount_zar=payload.amount_zar,
            currency=payload.currency,
            channel=payload.channel,
            merchant_category=payload.merchant_category,
            ip_address=payload.ip_address,
            device_fingerprint=payload.device_fingerprint,
            geolocation=payload.geolocation,
            metadata_=payload.metadata or {},
        )
        db.add(txn)
        await db.flush()  # get txn.id

        # ── audit: creation ────────────────────────────────────────────────
        db.add(AuditLog(
            transaction_id=txn.id,
            actor=f"api:{current_user.get('sub', 'unknown')}",
            action="TRANSACTION_CREATED",
            details={
                "channel": txn.channel,
                "amount_zar": txn.amount_zar,
            },
        ))

        # ── score ──────────────────────────────────────────────────────────
        try:
            risk_score = await score_transaction(db, txn)
        except Exception as exc:
            logger.error(
                "Scoring pipeline failed for txn=%s: %s",
                txn.id,
                exc,
                extra={"request_id": req_id},
            )
            txn.status = "declined"
            await db.commit()
            raise ScoringError(f"Failed to score transaction: {exc}") from exc

        # Update transaction status based on risk level
        if risk_score.risk_level in ("HIGH", "CRITICAL"):
            txn.status = "flagged"

        await db.commit()
        await db.refresh(txn)

        # ── publish alert (non-blocking) ───────────────────────────────────
        if txn.alerts:
            producer = getattr(request.app.state, "kafka_producer", None)
            for alert in txn.alerts:
                try:
                    await dispatch_alert(alert, kafka_producer=producer)
                except Exception as exc:
                    logger.warning(
                        "Failed to dispatch alert %s: %s",
                        alert.id,
                        exc,
                        extra={"request_id": req_id},
                    )

        # ── push raw event to Kafka ────────────────────────────────────────
        producer = getattr(request.app.state, "kafka_producer", None)
        if producer:
            try:
                await producer.send(
                    topic=settings.KAFKA_TRANSACTION_TOPIC,
                    value={
                        "transaction_id": txn.id,
                        "sender_id": txn.sender_id,
                        "receiver_id": txn.receiver_id,
                        "amount_zar": txn.amount_zar,
                        "risk_level": risk_score.risk_level,
                        "composite_score": risk_score.composite_score,
                    },
                    key=txn.id,
                )
                Metrics.kafka_messages_sent_total.labels(
                    topic=settings.KAFKA_TRANSACTION_TOPIC
                ).inc()
            except Exception as exc:
                logger.warning(
                    "Kafka publish failed for txn=%s: %s",
                    txn.id,
                    exc,
                    extra={"request_id": req_id},
                )
                Metrics.kafka_messages_errors_total.labels(
                    topic=settings.KAFKA_TRANSACTION_TOPIC
                ).inc()

        # ── record metrics ─────────────────────────────────────────────────
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_transaction_scored(
            txn.id,
            risk_score.risk_level,
            risk_score.composite_score,
            duration_ms,
        )
        Metrics.transactions_processed_total.labels(status="success").inc()
        Metrics.transaction_processing_duration_seconds.observe(duration_ms / 1000)

        return TransactionResponse.model_validate(txn)

    except (TransactionError, ScoringError) as exc:
        Metrics.transactions_processed_total.labels(status="error").inc()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(
            "Unexpected error in create_transaction: %s",
            exc,
            exc_info=True,
            extra={"request_id": req_id},
        )
        Metrics.transactions_processed_total.labels(status="error").inc()
        raise HTTPException(
            status_code=500,
            detail="Transaction processing failed"
        )


# ===========================================================================
# GET  /api/v1/transactions
# ===========================================================================
@router.get(
    "/",
    response_model=TransactionListResponse,
    summary="List Transactions",
    description="Paginated list with optional filters.",
)
async def list_transactions(
    page: int = 1,
    page_size: int = 25,
    status_filter: Optional[str] = None,
    sender_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List all transactions with optional filtering."""
    conditions = []

    if status_filter:
        conditions.append(Transaction.status == status_filter)
    if sender_id:
        conditions.append(Transaction.sender_id == sender_id)

    # Build statement
    stmt = (
        select(Transaction)
        .where(and_(*conditions) if conditions else True)
        .order_by(Transaction.created_at.desc())
    )

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total: int = count_result.scalar() or 0

    # Apply pagination
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    transactions = list(result.scalars())

    return TransactionListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[TransactionResponse.model_validate(t) for t in transactions],
    )


# ===========================================================================
# GET  /api/v1/transactions/{txn_id}
# ===========================================================================
@router.get(
    "/{txn_id}",
    response_model=dict,
    summary="Get Transaction Details",
    description="Retrieve a transaction with full scoring breakdown and audit trail.",
)
async def get_transaction(
    txn_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get detailed transaction information."""
    stmt = select(Transaction).where(Transaction.id == txn_id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()

    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found"
        )

    # Build response with risk score and audit logs
    return {
        "transaction": TransactionResponse.model_validate(txn),
        "risk_score": (
            RiskScoreResponse.model_validate(txn.risk_score)
            if txn.risk_score
            else None
        ),
        "alerts": [
            {
                "id": alert.id,
                "severity": alert.severity,
                "type": alert.alert_type,
                "message": alert.message,
                "status": alert.status,
            }
            for alert in txn.alerts
        ],
        "audit_logs": [
            {
                "actor": log.actor,
                "action": log.action,
                "details": log.details,
                "created_at": log.created_at.isoformat(),
            }
            for log in txn.audit_logs
        ],
    }

