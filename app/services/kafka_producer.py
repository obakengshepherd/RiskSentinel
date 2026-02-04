"""
RiskSentinel — Kafka Producer & Consumer

Producer  → called by the /transactions endpoint to push raw events.
Consumer  → background worker that pulls from the raw topic, scores, and
            publishes the scored event + alert (if any) downstream.
"""

import json
import logging
from typing import Any, Dict

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

from app.config import settings

logger = logging.getLogger("risksentinel.kafka")


# ===========================================================================
# Producer (singleton, lifecycle managed by FastAPI app.state)
# ===========================================================================
class KafkaProducer:
    def __init__(self):
        self._producer: AIOKafkaProducer | None = None

    async def start(self):
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks="all",              # strongest durability guarantee
            compression_type="gzip",
        )
        await self._producer.start()
        logger.info("Kafka producer started — brokers=%s", settings.KAFKA_BOOTSTRAP_SERVERS)

    async def stop(self):
        if self._producer:
            await self._producer.stop()
            logger.info("Kafka producer stopped.")

    async def send(self, topic: str, value: Dict[str, Any], key: str | None = None):
        if self._producer is None:
            raise RuntimeError("KafkaProducer has not been started.")
        await self._producer.send(
            topic=topic,
            value=value,
            key=key,
        )
        logger.debug("Kafka send → topic=%s key=%s", topic, key)


# ===========================================================================
# Consumer  (run as a background asyncio task)
# ===========================================================================
class KafkaConsumer:
    """
    Subscribes to the raw-transactions topic, scores each message,
    and publishes results to the scored topic.

    Instantiate and call `run()` inside an asyncio task — e.g. from a
    management script or a separate Kubernetes Job / Deployment.
    """

    def __init__(self):
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: KafkaProducer = KafkaProducer()
        self._running = False

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            settings.KAFKA_TRANSACTION_TOPIC,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            auto_commit_interval_ms=1_000,
        )
        await self._consumer.start()
        await self._producer.start()
        self._running = True
        logger.info("Kafka consumer started — group=%s", settings.KAFKA_CONSUMER_GROUP)

    async def stop(self):
        self._running = False
        if self._consumer:
            await self._consumer.stop()
        await self._producer.stop()
        logger.info("Kafka consumer stopped.")

    async def run(self):
        """Main loop — override _process_message for custom logic."""
        await self.start()
        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                logger.debug(
                    "Kafka recv ← topic=%s partition=%d offset=%d",
                    msg.topic, msg.partition, msg.offset,
                )
                await self._process_message(msg.value)
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Hook for scoring pipeline
    # ------------------------------------------------------------------
    async def _process_message(self, payload: Dict[str, Any]):
        """
        Score the transaction and publish the result.

        In production this imports scorer.score_transaction and runs it
        inside a DB session.  Shown here as the integration point.
        """
        from app.services.db import AsyncSessionLocal
        from app.models.models import Transaction
        from app.services.scorer import score_transaction

        async with AsyncSessionLocal() as db:
            # Upsert / fetch the transaction row
            from sqlalchemy import select
            stmt = select(Transaction).where(Transaction.external_id == payload.get("external_id"))
            result = await db.execute(stmt)
            txn = result.scalar_one_or_none()

            if txn is None:
                txn = Transaction(
                    sender_id=payload["sender_id"],
                    receiver_id=payload["receiver_id"],
                    amount_zar=payload["amount_zar"],
                    currency=payload.get("currency", "ZAR"),
                    channel=payload["channel"],
                    external_id=payload.get("external_id"),
                    merchant_category=payload.get("merchant_category"),
                    ip_address=payload.get("ip_address"),
                    device_fingerprint=payload.get("device_fingerprint"),
                    geolocation=payload.get("geolocation"),
                    metadata_=payload.get("metadata", {}),
                )
                db.add(txn)
                await db.flush()

            risk_score = await score_transaction(db, txn)
            await db.commit()

            # Publish scored event downstream
            scored_event = {
                "transaction_id": txn.id,
                "composite_score": risk_score.composite_score,
                "risk_level": risk_score.risk_level,
                "triggered_rules": risk_score.triggered_rules,
            }
            await self._producer.send(
                topic=settings.KAFKA_SCORED_TOPIC,
                value=scored_event,
                key=txn.id,
            )
            logger.info("Scored event published → topic=%s txn=%s", settings.KAFKA_SCORED_TOPIC, txn.id)
