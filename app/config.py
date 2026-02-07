"""
RiskSentinel — Centralised Configuration
All tunables live here; override via environment variables or a .env file.
Supports environment-specific settings: development | staging | production
"""

from pydantic_settings import BaseSettings
from typing import List
from datetime import timedelta


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = "RiskSentinel"
    APP_ENV: str = "development"          # development | staging | production
    APP_PORT: int = 8000
    SECRET_KEY: str = "change-me-in-production"  # MUST override in production
    DEBUG: bool = False

    # ------------------------------------------------------------------
    # Database  (async asyncpg)
    # ------------------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+asyncpg://risksentinel:password@localhost:5432/risksentinel"
    )
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 40
    DATABASE_POOL_RECYCLE: int = 3600  # recycle connections after 1 hour

    # ------------------------------------------------------------------
    # Kafka
    # ------------------------------------------------------------------
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TRANSACTION_TOPIC: str = "rs.transactions.raw"
    KAFKA_SCORED_TOPIC: str = "rs.transactions.scored"
    KAFKA_ALERT_TOPIC: str = "rs.alerts"
    KAFKA_CONSUMER_GROUP: str = "risksentinel-scorer"
    KAFKA_TIMEOUT_MS: int = 10_000

    # ------------------------------------------------------------------
    # Risk / Scoring Thresholds  (tunable without code changes)
    # ------------------------------------------------------------------
    RISK_SCORE_HIGH: float = 0.7          # >= triggers HIGH flag
    RISK_SCORE_CRITICAL: float = 0.9      # >= triggers CRITICAL flag + alert
    VELOCITY_WINDOW_SECONDS: int = 300    # 5-minute sliding window
    VELOCITY_MAX_TXN_COUNT: int = 10      # max transactions in window
    VELOCITY_MAX_TOTAL_ZAR: float = 50_000.0  # max cumulative ZAR in window
    AMOUNT_ANOMALY_ZSCORE: float = 3.0    # z-score threshold for amount anomaly

    # ------------------------------------------------------------------
    # ML
    # ------------------------------------------------------------------
    ML_MODEL_PATH: str = "ml/models/anomaly_model.pkl"
    ML_ENABLED: bool = True

    # ------------------------------------------------------------------
    # Authentication & Authorization
    # ------------------------------------------------------------------
    AUTH_ENABLED: bool = True
    JWT_SECRET_KEY: str = "change-me-in-production"  # MUST override in production
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    JWT_REFRESH_EXPIRATION_DAYS: int = 7
    API_KEY_ENABLED: bool = True           # allow API key as fallback to JWT

    # ------------------------------------------------------------------
    # Rate Limiting
    # ------------------------------------------------------------------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 100  # per IP/user
    RATE_LIMIT_REQUESTS_PER_SECOND: int = 10   # sustained burst limit

    # ------------------------------------------------------------------
    # CORS / Hosting
    # ------------------------------------------------------------------
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000"
    ]
    ALLOWED_HOSTS: List[str] = ["localhost", "127.0.0.1"]

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # json | text
    STRUCTURED_LOGGING_ENABLED: bool = True

    # ------------------------------------------------------------------
    # Request Validation
    # ------------------------------------------------------------------
    MAX_TRANSACTION_AMOUNT_ZAR: float = 10_000_000.0
    MIN_TRANSACTION_AMOUNT_ZAR: float = 0.01
    MAX_REQUEST_SIZE_MB: int = 10

    # ------------------------------------------------------------------
    # Monitoring & Observability
    # ------------------------------------------------------------------
    METRICS_ENABLED: bool = True
    METRICS_PORT: int = 9090
    ENABLE_REQUEST_TRACING: bool = True

    # ------------------------------------------------------------------
    # pydantic-settings: read from .env automatically
    # ------------------------------------------------------------------
    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def jwt_expiration(self) -> timedelta:
        return timedelta(hours=self.JWT_EXPIRATION_HOURS)

    @property
    def jwt_refresh_expiration(self) -> timedelta:
        return timedelta(days=self.JWT_REFRESH_EXPIRATION_DAYS)

    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    def is_development(self) -> bool:
        return self.APP_ENV.lower() == "development"


# Singleton
settings = Settings()
