"""
RiskSentinel — Pydantic Schemas (Request / Response DTOs)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ===========================================================================
# Transaction
# ===========================================================================
class TransactionCreate(BaseModel):
    """Inbound payload from payment gateway or test harness."""
    external_id: Optional[str] = None
    sender_id: str
    receiver_id: str
    amount_zar: float = Field(..., gt=0, description="Transaction amount in ZAR")
    currency: str = Field(default="ZAR", max_length=3)
    channel: str                                          # mobile_banking | api | ussd | pos
    merchant_category: Optional[str] = None
    ip_address: Optional[str] = None
    device_fingerprint: Optional[str] = None
    geolocation: Optional[Dict[str, float]] = None       # {"lat": -33.92, "lng": 18.42}
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class TransactionResponse(BaseModel):
    id: str
    external_id: Optional[str]
    sender_id: str
    receiver_id: str
    amount_zar: float
    currency: str
    channel: str
    status: str
    risk_level: Optional[str] = None
    composite_score: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[TransactionResponse]


# ===========================================================================
# Risk Score
# ===========================================================================
class RiskScoreResponse(BaseModel):
    transaction_id: str
    composite_score: float
    rule_score: float
    velocity_score: float
    anomaly_score: float
    ml_score: Optional[float]
    risk_level: str
    triggered_rules: List[str]
    explanation: Dict[str, Any]
    scored_at: datetime

    model_config = {"from_attributes": True}


# ===========================================================================
# Fraud Rule
# ===========================================================================
class FraudRuleCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    weight: float = Field(default=0.1, gt=0, le=1.0)
    condition: Dict[str, Any]                             # flexible JSON definition


class FraudRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    weight: Optional[float] = Field(default=None, gt=0, le=1.0)
    condition: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class FraudRuleResponse(BaseModel):
    id: str
    code: str
    name: str
    description: Optional[str]
    weight: float
    condition: Dict[str, Any]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ===========================================================================
# Alert
# ===========================================================================
class AlertResponse(BaseModel):
    id: str
    transaction_id: str
    severity: str
    alert_type: str
    message: str
    status: str
    assigned_to: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertUpdate(BaseModel):
    status: Optional[str] = None                          # acknowledged | resolved | closed
    assigned_to: Optional[str] = None


class AlertListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[AlertResponse]


# ===========================================================================
# Dashboard
# ===========================================================================
class DashboardSummary(BaseModel):
    total_transactions: int
    total_alerts_open: int
    total_alerts_critical: int
    avg_risk_score: float
    top_risk_transactions: List[TransactionResponse]
    alert_distribution: Dict[str, int]                    # severity -> count
    velocity_breaches_last_hour: int


# ===========================================================================
# Health
# ===========================================================================
class HealthCheck(BaseModel):
    status: str                                           # healthy | degraded | unhealthy
    db: str
    kafka: str
    uptime_seconds: float
    version: str
