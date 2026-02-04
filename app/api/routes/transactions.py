"""
RiskSentinel — Transactions API
POST /api/v1/transactions            → ingest + score in one call
GET  /api/v1/transactions            → paginated list with filters
GET  /api/v1/transactions/{txn_id}   → single transaction + score + audit
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog, Transaction
from app.models.schemas import (
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
    RiskScoreResponse,
)
from app.services.db import get_db
from app.services.scorer import score_transaction
from app.services.alerting import dispatch_alert
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
):
    # ── persist ────────────────────────────────────────────────────────────
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
    await db.flush()                       # get txn.id

    # ── audit: creation ────────────────────────────────────────────────────
    db.add(AuditLog(
        transaction_id=txn.id,
        actor="system",
        action="TRANSACTION_CREATED",
        details={"channel": txn.channel, "amount_zar": txn.amount_zar},
    ))

    # ── score ──────────────────────────────────────────────────────────────
    risk_score = await score_transaction(db, txn)
    await db.commit()
    await db.refresh(txn)

    # ── publish alert (non-blocking) ───────────────────────────────────────
    if txn.risk_score and txn.alerts:
        producer = getattr(request.app.state, "kafka_producer", None)
        for alert in txn.alerts:
            await dispatch_alert(alert, kafka_producer=producer)

    # ── also push raw event to Kafka (fire-and-forget) ─────────────────────
    producer = getattr(request.app.state, "kafka_producer", None)
    if producer:
        try:
            await producer.send(
                topic=settings.KAFKA_TRANSACTION_TOPIC,
                value={
                    "transaction_id": txn.id,
                    "sender_id": txn.sender_id,
                    "amount_zar": txn.amount_zar,
                    "risk_level": risk_score.risk_level,
                },
                key=txn.id,
            )
        except Exception as exc:
            logger.warning("Kafka send failed (non-critical): %s", exc)

    return _txn_to_response(txn, risk_score)


# ===========================================================================
# GET  /api/v1/transactions
# ===========================================================================
@router.get(
    "/",
    response_model=TransactionListResponse,
    summary="List Transactions",
    description="Paginated list with optional filters on status, sender, risk_level.",
)
async def list_transactions(
    page: int = 1,
    page_size: int = 20,
    status_filter: Optional[str] = None,
    sender_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if status_filter:
        conditions.append(Transaction.status == status_filter)
    if sender_id:
        conditions.append(Transaction.sender_id == sender_id)

    # total count
    count_stmt = select(Transaction).where(and_(*conditions)) if conditions else select(Transaction)
    from sqlalchemy import func
    count_result = await db.execute(
        select(func.count()).select_from(count_stmt.subquery())
    )
    total: int = count_result.scalar()  # type: ignore[assignment]

    # paginated fetch
    stmt = (
        select(Transaction)
        .where(and_(*conditions)) if conditions
        else select(Transaction)
    )
    stmt = stmt.order_by(Transaction.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    transactions = list(result.scalars())

    items = [_txn_to_response(t, t.risk_score) for t in transactions]

    return TransactionListResponse(total=total, page=page, page_size=page_size, items=items)


# ===========================================================================
# GET  /api/v1/transactions/{txn_id}
# ===========================================================================
@router.get(
    "/{txn_id}",
    response_model=TransactionResponse,
    summary="Get Transaction Detail",
)
async def get_transaction(
    txn_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Transaction).where(Transaction.id == txn_id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found.")

    return _txn_to_response(txn, txn.risk_score)


# ===========================================================================
# GET  /api/v1/transactions/{txn_id}/score
# ===========================================================================
@router.get(
    "/{txn_id}/score",
    response_model=RiskScoreResponse,
    summary="Get Risk Score for a Transaction",
)
async def get_transaction_score(
    txn_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Transaction).where(Transaction.id == txn_id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found.")
    if txn.risk_score is None:
        raise HTTPException(status_code=404, detail="Risk score not yet computed.")

    return txn.risk_score


# ===========================================================================
# Helper
# ===========================================================================
def _txn_to_response(txn: Transaction, risk_score=None) -> TransactionResponse:
    return TransactionResponse(
        id=txn.id,
        external_id=txn.external_id,
        sender_id=txn.sender_id,
        receiver_id=txn.receiver_id,
        amount_zar=txn.amount_zar,
        currency=txn.currency,
        channel=txn.channel,
        status=txn.status,
        risk_level=risk_score.risk_level if risk_score else None,
        composite_score=risk_score.composite_score if risk_score else None,
        created_at=txn.created_at,
    )
