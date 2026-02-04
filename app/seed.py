"""
RiskSentinel — Seed Data Script
Inserts the default fraud rules defined in app/rules/default_rules.py
if the fraud_rules table is empty.

Usage (run once after DB migration):
    python -m app.seed
"""

import asyncio
import logging

from sqlalchemy import select, func
from app.services.db import AsyncSessionLocal, init_db
from app.models.models import FraudRule
from app.rules.default_rules import DEFAULT_RULES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("risksentinel.seed")


async def seed():
    await init_db()                                    # ensure tables exist

    async with AsyncSessionLocal() as db:
        count_result = await db.execute(
            select(func.count()).select_from(FraudRule)
        )
        existing: int = count_result.scalar()  # type: ignore[assignment]

        if existing > 0:
            logger.info("fraud_rules already has %d rows — skipping seed.", existing)
            return

        logger.info("Inserting %d default fraud rules …", len(DEFAULT_RULES))
        for rule_data in DEFAULT_RULES:
            rule = FraudRule(**rule_data)
            db.add(rule)

        await db.commit()
        logger.info("Seed complete — %d rules inserted.", len(DEFAULT_RULES))


if __name__ == "__main__":
    asyncio.run(seed())
