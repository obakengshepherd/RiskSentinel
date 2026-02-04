"""
RiskSentinel — Centralised Configuration
All tunables live here; override via environment variables or a .env file.
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = "RiskSentinel"
    APP_ENV: str = "development"          # development | staging | production
    APP_PORT: int = 8000
    SECRET_KEY: str = "change-me-in-production"

    # ------------------------------------------------------------------
    # Database  (async asyncpg)
    # ------------------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+asyncpg://risksentinel:password@localhost:5432/risksentinel"
    )

    # ------------------------------------------------------------------
    # Kafka
    # ------------------------------------------------------------------
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TRANSACTION_TOPIC: str = "rs.transactions.raw"
    KAFKA_SCORED_TOPIC: str = "rs.transactions.scored"
    KAFKA_ALERT_TOPIC: str = "rs.alerts"
    KAFKA_CONSUMER_GROUP: str = "risksentinel-scorer"

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
    # CORS / Hosting
    # ------------------------------------------------------------------
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]
    ALLOWED_HOSTS: List[str] = ["*"]

    # ------------------------------------------------------------------
    # pydantic-settings: read from .env automatically
    # ------------------------------------------------------------------
    model_config = {"env_file": ".env", "extra": "ignore"}


# Singleton
settings = Settings()
