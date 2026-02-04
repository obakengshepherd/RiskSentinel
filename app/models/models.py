"""
RiskSentinel — ORM Models (PostgreSQL)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.services.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid4():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------
class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_sender_created", "sender_id", "created_at"),
        Index("ix_transactions_receiver_created", "receiver_id", "created_at"),
        Index("ix_transactions_status", "status"),
    )

    id: str = Column(String(36), primary_key=True, default=_uuid4)
    external_id: str = Column(String(128), unique=True, nullable=True)   # upstream ref
    sender_id: str = Column(String(128), nullable=False)
    receiver_id: str = Column(String(128), nullable=False)
    amount_zar: float = Column(Float, nullable=False)
    currency: str = Column(String(3), default="ZAR")
    channel: str = Column(String(64), nullable=False)   # e.g. mobile_banking, api, ussd
    merchant_category: str = Column(String(128), nullable=True)
    ip_address: str = Column(String(45), nullable=True)
    device_fingerprint: str = Column(String(256), nullable=True)
    geolocation: dict = Column(JSONB, nullable=True)    # {"lat": ..., "lng": ...}
    status: str = Column(String(32), default="pending") # pending | approved | declined | flagged
    metadata_: dict = Column("metadata", JSONB, default=dict)
    created_at: datetime = Column(DateTime(timezone=True), default=_utcnow)
    updated_at: datetime = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # relationships
    risk_score = relationship("RiskScore", back_populates="transaction", uselist=False)
    alerts = relationship("Alert", back_populates="transaction")
    audit_logs = relationship("AuditLog", back_populates="transaction")


# ---------------------------------------------------------------------------
# RiskScore  (1-to-1 with Transaction)
# ---------------------------------------------------------------------------
class RiskScore(Base):
    __tablename__ = "risk_scores"

    id: str = Column(String(36), primary_key=True, default=_uuid4)
    transaction_id: str = Column(String(36), ForeignKey("transactions.id", ondelete="CASCADE"), unique=True, nullable=False)
    composite_score: float = Column(Float, nullable=False)                    # 0.0 – 1.0
    rule_score: float = Column(Float, default=0.0)
    velocity_score: float = Column(Float, default=0.0)
    anomaly_score: float = Column(Float, default=0.0)
    ml_score: float = Column(Float, nullable=True)
    risk_level: str = Column(String(16), default="LOW")                       # LOW | MEDIUM | HIGH | CRITICAL
    triggered_rules: list = Column(JSONB, default=list)                       # rule codes that fired
    explanation: dict = Column(JSONB, default=dict)                           # human-readable breakdown
    scored_at: datetime = Column(DateTime(timezone=True), default=_utcnow)

    transaction = relationship("Transaction", back_populates="risk_score")


# ---------------------------------------------------------------------------
# FraudRule  (dynamic, CRUD-able rule definitions)
# ---------------------------------------------------------------------------
class FraudRule(Base):
    __tablename__ = "fraud_rules"

    id: str = Column(String(36), primary_key=True, default=_uuid4)
    code: str = Column(String(64), unique=True, nullable=False)              # e.g. "RULE_HIGH_AMOUNT"
    name: str = Column(String(128), nullable=False)
    description: str = Column(Text, nullable=True)
    weight: float = Column(Float, default=0.1)                               # contribution to composite score
    condition: dict = Column(JSONB, nullable=False)                           # JSON rule definition
    is_active: bool = Column(Boolean, default=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_utcnow)
    updated_at: datetime = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------
class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_severity_created", "severity", "created_at"),
        Index("ix_alerts_status", "status"),
    )

    id: str = Column(String(36), primary_key=True, default=_uuid4)
    transaction_id: str = Column(String(36), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    severity: str = Column(String(16), nullable=False)                        # LOW | MEDIUM | HIGH | CRITICAL
    alert_type: str = Column(String(64), nullable=False)                      # FRAUD_SUSPECTED | VELOCITY_BREACH | …
    message: str = Column(Text, nullable=False)
    status: str = Column(String(32), default="open")                          # open | acknowledged | resolved | closed
    assigned_to: str = Column(String(128), nullable=True)
    resolved_at: datetime = Column(DateTime(timezone=True), nullable=True)
    metadata_: dict = Column("metadata", JSONB, default=dict)
    created_at: datetime = Column(DateTime(timezone=True), default=_utcnow)
    updated_at: datetime = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    transaction = relationship("Transaction", back_populates="alerts")


# ---------------------------------------------------------------------------
# AuditLog  (immutable append-only)
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_transaction_created", "transaction_id", "created_at"),
        Index("ix_audit_logs_actor", "actor"),
    )

    id: str = Column(String(36), primary_key=True, default=_uuid4)
    transaction_id: str = Column(String(36), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=True)
    actor: str = Column(String(128), nullable=False)                          # system | analyst:<email>
    action: str = Column(String(64), nullable=False)                          # TRANSACTION_CREATED | SCORED | …
    details: dict = Column(JSONB, default=dict)
    created_at: datetime = Column(DateTime(timezone=True), default=_utcnow)
