"""
RiskSentinel — Alerting Service

Centralised dispatcher.  Alerts are already persisted by the scorer;
this module handles the *outbound* fan-out:
    • Kafka alert topic  (always)
    • Webhook  (configurable per severity)
    • Structured log     (always)

Extend by adding a new channel (e.g. PagerDuty, email) in _dispatch().
"""

import logging
from typing import Any, Dict

import httpx

from app.config import settings
from app.models.models import Alert

logger = logging.getLogger("risksentinel.alerts")

# ---------------------------------------------------------------------------
# Webhook map — severity → URL.  Set via environment / config in production.
# ---------------------------------------------------------------------------
WEBHOOK_URLS: Dict[str, str] = {
    # "CRITICAL": "https://hooks.example.com/critical",
    # "HIGH":     "https://hooks.example.com/high",
}


# ===========================================================================
# Public entry-point
# ===========================================================================
async def dispatch_alert(alert: Alert, kafka_producer=None):
    """
    Fan out the alert across all configured channels.

    Parameters
    ----------
    alert          : the persisted Alert ORM row
    kafka_producer : the app.state.kafka_producer singleton (optional)
    """
    payload = _alert_to_dict(alert)

    # 1. Structured log (always)
    logger.warning(
        "ALERT [%s] id=%s type=%s txn=%s — %s",
        alert.severity, alert.id, alert.alert_type,
        alert.transaction_id, alert.message,
    )

    # 2. Kafka (if producer available)
    if kafka_producer:
        try:
            await kafka_producer.send(
                topic=settings.KAFKA_ALERT_TOPIC,
                value=payload,
                key=alert.transaction_id,
            )
            logger.debug("Alert pushed to Kafka topic=%s", settings.KAFKA_ALERT_TOPIC)
        except Exception as exc:
            logger.error("Kafka alert send failed: %s", exc)

    # 3. Webhook (if URL configured for this severity)
    webhook_url = WEBHOOK_URLS.get(alert.severity)
    if webhook_url:
        await _send_webhook(webhook_url, payload)


# ===========================================================================
# Helpers
# ===========================================================================
def _alert_to_dict(alert: Alert) -> Dict[str, Any]:
    return {
        "alert_id": alert.id,
        "transaction_id": alert.transaction_id,
        "severity": alert.severity,
        "alert_type": alert.alert_type,
        "message": alert.message,
        "status": alert.status,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


async def _send_webhook(url: str, payload: Dict[str, Any]):
    """Fire-and-forget HTTP POST; logs errors but never raises."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("Webhook delivered → %s (status %d)", url, resp.status_code)
    except Exception as exc:
        logger.error("Webhook delivery failed (%s): %s", url, exc)
