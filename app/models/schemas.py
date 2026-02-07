"""
RiskSentinel — Pydantic Schemas (Request / Response DTOs)

All schemas include:
- Input validation with constraints
- Type hints and descriptions
- Configuration for ORM serialization
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, ConfigDict
from ipaddress import ip_address

from app.config import settings


# ===========================================================================
# Transaction
# ===========================================================================
class TransactionCreate(BaseModel):
    """Inbound payload from payment gateway or test harness."""
    external_id: Optional[str] = Field(
        default=None,
        max_length=128,
        pattern=r'^[a-zA-Z0-9\-_.]+$',
        description="External transaction reference"
    )
    sender_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r'^[a-zA-Z0-9\-_]+$',
        description="Sender identifier"
    )
    receiver_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r'^[a-zA-Z0-9\-_]+$',
        description="Receiver identifier"
    )
    amount_zar: float = Field(
        ...,
        gt=settings.MIN_TRANSACTION_AMOUNT_ZAR,
        le=settings.MAX_TRANSACTION_AMOUNT_ZAR,
        description="Transaction amount in ZAR"
    )
    currency: str = Field(
        default="ZAR",
        max_length=3,
        description="ISO 4217 currency code"
    )
    channel: str = Field(
        ...,
        description="Channel: mobile_banking | api | ussd | pos"
    )
    merchant_category: Optional[str] = Field(
        default=None,
        max_length=128,
        description="ISO 18245 merchant category"
    )
    ip_address: Optional[str] = Field(
        default=None,
        description="IPv4 or IPv6 address"
    )
    device_fingerprint: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Device fingerprint hash"
    )
    geolocation: Optional[Dict[str, float]] = Field(
        default=None,
        description="Lat/lng coordinates"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Custom metadata (max 10MB)"
    )

    @field_validator("amount_zar")
    @classmethod
    def amount_must_be_reasonable(cls, v: float) -> float:
        """Validate transaction amount is reasonable."""
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, v: Optional[str]) -> Optional[str]:
        """Validate IP address format."""
        if v:
            try:
                ip_address(v)
            except ValueError:
                raise ValueError(f"Invalid IP address: {v}")
        return v

    @field_validator("geolocation")
    @classmethod
    def validate_geolocation(cls, v: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
        """Validate geolocation coordinates."""
        if v:
            if "lat" not in v or "lng" not in v:
                raise ValueError("Geolocation must have 'lat' and 'lng'")
            if not (-90 <= v["lat"] <= 90):
                raise ValueError("Latitude must be between -90 and 90")
            if not (-180 <= v["lng"] <= 180):
                raise ValueError("Longitude must be between -180 and 180")
        return v


class TransactionResponse(BaseModel):
    """Outbound transaction response."""
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

    model_config = ConfigDict(from_attributes=True)


class TransactionListResponse(BaseModel):
    """Paginated transaction list."""
    total: int
    page: int
    page_size: int
    items: List[TransactionResponse]


# ===========================================================================
# Risk Score
# ===========================================================================
class RiskScoreResponse(BaseModel):
    """Detailed risk score breakdown."""
    transaction_id: str
    composite_score: float = Field(..., ge=0.0, le=1.0)
    rule_score: float = Field(..., ge=0.0, le=1.0)
    velocity_score: float = Field(..., ge=0.0, le=1.0)
    anomaly_score: float = Field(..., ge=0.0, le=1.0)
    ml_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    risk_level: str
    triggered_rules: List[str]
    explanation: Dict[str, Any]
    scored_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Fraud Rule
# ===========================================================================
class FraudRuleCreate(BaseModel):
    """Create a new fraud rule."""
    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r'^[A-Z0-9_]+$',
        description="Unique rule code (uppercase + underscores)"
    )
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    weight: float = Field(default=0.1, gt=0, le=1.0)
    condition: Dict[str, Any] = Field(..., description="JSON rule condition")


class FraudRuleUpdate(BaseModel):
    """Update an existing fraud rule."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    weight: Optional[float] = Field(default=None, gt=0, le=1.0)
    condition: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class FraudRuleResponse(BaseModel):
    """Fraud rule response."""
    id: str
    code: str
    name: str
    description: Optional[str]
    weight: float
    condition: Dict[str, Any]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Alert
# ===========================================================================
class AlertResponse(BaseModel):
    """Alert response."""
    id: str
    transaction_id: str
    severity: str
    alert_type: str
    message: str
    status: str
    assigned_to: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AlertUpdate(BaseModel):
    """Update an alert."""
    status: Optional[str] = Field(
        default=None,
        description="acknowledged | resolved | closed"
    )
    assigned_to: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Analyst email or identifier"
    )


class AlertListResponse(BaseModel):
    """Paginated alert list."""
    total: int
    page: int
    page_size: int
    items: List[AlertResponse]


# ===========================================================================
# Dashboard
# ===========================================================================
class DashboardSummary(BaseModel):
    """Dashboard summary stats."""
    total_transactions: int
    total_alerts_open: int
    total_alerts_critical: int
    avg_risk_score: float
    top_risk_transactions: List[TransactionResponse]
    alert_distribution: Dict[str, int]
    velocity_breaches_last_hour: int


# ===========================================================================
# Health & Metrics
# ===========================================================================
class HealthCheck(BaseModel):
    """Health check response."""
    status: str  # healthy | degraded | unhealthy
    db: str
    kafka: str
    uptime_seconds: float
    version: str


class MetricsResponse(BaseModel):
    """Prometheus metrics endpoint."""
    pass  # Prometheus client handles serialization


# ===========================================================================
# Authentication
# ===========================================================================
class TokenResponse(BaseModel):
    """JWT token response."""
    access_token: str = Field(..., description="Access token (JWT)")
    refresh_token: str = Field(..., description="Refresh token")
    token_type: str = Field(default="bearer")
    expires_in: int = Field(..., description="Token expiration in seconds")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
                "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
                "token_type": "bearer",
                "expires_in": 86400,
            }
        }
    )


class LoginRequest(BaseModel):
    """Login request."""
    username: str
    password: str


class AuthorizedUser(BaseModel):
    """Currently authenticated user info."""
    sub: str = Field(..., description="User subject/ID")
    scopes: List[str] = Field(..., description="User permissions")
    exp: Optional[int] = None

