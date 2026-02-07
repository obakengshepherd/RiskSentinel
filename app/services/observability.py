"""
RiskSentinel — Observability Layer

Provides:
- Structured JSON logging
- Prometheus metrics
- Request correlation IDs
- Performance monitoring
"""

import logging
import json
import time
from typing import Optional, Dict, Any
from contextvars import ContextVar

from prometheus_client import Counter, Histogram, Gauge
from pythonjsonlogger import jsonlogger
from fastapi import Request, Response

from app.config import settings

# ===========================================================================
# Context Variables (for distributed tracing)
# ===========================================================================
REQUEST_ID_CTX: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
USER_ID_CTX: ContextVar[Optional[str]] = ContextVar("user_id", default=None)


def get_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return REQUEST_ID_CTX.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID in context."""
    REQUEST_ID_CTX.set(request_id)


def get_user_id() -> Optional[str]:
    """Get the current user ID from context."""
    return USER_ID_CTX.get()


def set_user_id(user_id: str) -> None:
    """Set the user ID in context."""
    USER_ID_CTX.set(user_id)


# ===========================================================================
# Structured Logging
# ===========================================================================
class StructuredLogFormatter(jsonlogger.JsonFormatter):
    """Custom JSON log formatter with additional context fields."""

    def add_fields(
        self,
        log_record: Dict[str, Any],
        record: logging.LogRecord,
        message_dict: Dict[str, Any],
    ) -> None:
        """Add context variables to log record."""
        super().add_fields(log_record, record, message_dict)

        # Add context variables
        request_id = get_request_id()
        user_id = get_user_id()

        if request_id:
            log_record["request_id"] = request_id
        if user_id:
            log_record["user_id"] = user_id

        # Add environment info
        log_record["env"] = settings.APP_ENV
        log_record["service"] = settings.APP_NAME
        log_record["version"] = "1.0.0"


def setup_logging() -> None:
    """Configure structured JSON logging for the application."""
    if not settings.STRUCTURED_LOGGING_ENABLED:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    # Remove default handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler with JSON formatter
    console_handler = logging.StreamHandler()
    formatter = StructuredLogFormatter()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Set all loggers to the same level
    for logger_name in [
        "risksentinel",
        "fastapi",
        "uvicorn",
        "sqlalchemy",
    ]:
        logging.getLogger(logger_name).setLevel(settings.LOG_LEVEL)


# ===========================================================================
# Prometheus Metrics
# ===========================================================================
class Metrics:
    """Application metrics collector."""

    # Request metrics
    http_requests_total = Counter(
        "risksentinel_http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status"],
    )

    http_request_duration_seconds = Histogram(
        "risksentinel_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "endpoint"],
        buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    )

    # Transaction metrics
    transactions_processed_total = Counter(
        "risksentinel_transactions_processed_total",
        "Total transactions processed",
        ["status"],  # success, error
    )

    transaction_processing_duration_seconds = Histogram(
        "risksentinel_transaction_processing_duration_seconds",
        "Transaction processing time",
        buckets=(0.01, 0.05, 0.1, 0.5, 1.0),
    )

    # Scoring metrics
    scoring_pipeline_duration_seconds = Histogram(
        "risksentinel_scoring_pipeline_duration_seconds",
        "Scoring pipeline execution time",
        ["signal"],  # rules, velocity, anomaly, ml
    )

    risk_scores_distribution = Histogram(
        "risksentinel_risk_scores_distribution",
        "Distribution of risk scores",
        buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    )

    risk_levels_total = Counter(
        "risksentinel_risk_levels_total",
        "Risk level distribution",
        ["level"],  # LOW, MEDIUM, HIGH, CRITICAL
    )

    alerts_fired_total = Counter(
        "risksentinel_alerts_fired_total",
        "Total alerts fired",
        ["severity", "type"],
    )

    # Database metrics
    db_queries_duration_seconds = Histogram(
        "risksentinel_db_queries_duration_seconds",
        "Database query duration",
        ["operation"],
    )

    db_connection_pool_size = Gauge(
        "risksentinel_db_connection_pool_size",
        "Current size of database connection pool",
    )

    # Kafka metrics
    kafka_messages_sent_total = Counter(
        "risksentinel_kafka_messages_sent_total",
        "Total Kafka messages sent",
        ["topic"],
    )

    kafka_messages_errors_total = Counter(
        "risksentinel_kafka_messages_errors_total",
        "Kafka message send errors",
        ["topic"],
    )

    # ML metrics
    ml_predictions_total = Counter(
        "risksentinel_ml_predictions_total",
        "ML model predictions",
        ["status"],  # success, error
    )

    ml_prediction_duration_seconds = Histogram(
        "risksentinel_ml_prediction_duration_seconds",
        "ML model inference time",
    )


# ===========================================================================
# Middleware for automatic metric collection
# ===========================================================================
async def metrics_middleware(request: Request, call_next) -> Response:
    """Record HTTP request metrics."""
    start_time = time.perf_counter()
    path = request.url.path.split("?")[0]  # Remove query string
    method = request.method

    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as exc:
        status_code = 500
        raise
    finally:
        duration = time.perf_counter() - start_time

        # Record metrics
        Metrics.http_requests_total.labels(
            method=method,
            endpoint=path,
            status=status_code,
        ).inc()

        Metrics.http_request_duration_seconds.labels(
            method=method,
            endpoint=path,
        ).observe(duration)

    return response


# ===========================================================================
# Logging utilities
# ===========================================================================
def log_transaction_scored(
    transaction_id: str,
    risk_level: str,
    composite_score: float,
    duration_ms: float,
) -> None:
    """Log a transaction scoring event."""
    logger = logging.getLogger("risksentinel.scoring")
    logger.info(
        "Transaction scored",
        extra={
            "transaction_id": transaction_id,
            "risk_level": risk_level,
            "composite_score": composite_score,
            "duration_ms": duration_ms,
        },
    )

    # Record metrics
    Metrics.risk_scores_distribution.observe(composite_score)
    Metrics.risk_levels_total.labels(level=risk_level).inc()


def log_alert_fired(
    alert_id: str,
    transaction_id: str,
    severity: str,
    alert_type: str,
) -> None:
    """Log an alert event."""
    logger = logging.getLogger("risksentinel.alerts")
    logger.warning(
        "Alert fired",
        extra={
            "alert_id": alert_id,
            "transaction_id": transaction_id,
            "severity": severity,
            "alert_type": alert_type,
        },
    )

    # Record metrics
    Metrics.alerts_fired_total.labels(
        severity=severity,
        type=alert_type,
    ).inc()
