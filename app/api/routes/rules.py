"""
RiskSentinel — Fraud Rules API  (CRUD)
POST   /api/v1/rules            → create a rule
GET    /api/v1/rules            → list all rules (with active/inactive filter)
GET    /api/v1/rules/{rule_id}  → single rule
PUT    /api/v1/rules/{rule_id}  → full update
PATCH  /api/v1/rules/{rule_id}  → partial update (toggle active, adjust weight …)
DELETE /api/v1/rules/{rule_id}  → soft-delete (sets is_active = False)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import FraudRule
from app.models.schemas import FraudRuleCreate, FraudRuleResponse, FraudRuleUpdate
from app.services.db import get_db

logger = logging.getLogger("risksentinel.api.rules")
router = APIRouter()


# ===========================================================================
# POST  /api/v1/rules
# ===========================================================================
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FraudRuleResponse,
    summary="Create a Fraud Rule",
)
async def create_rule(
    body: FraudRuleCreate,
    db: AsyncSession = Depends(get_db),
):
    # duplicate code check
    existing = await db.execute(select(FraudRule).where(FraudRule.code == body.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Rule code '{body.code}' already exists.")

    rule = FraudRule(
        code=body.code,
        name=body.name,
        description=body.description,
        weight=body.weight,
        condition=body.condition,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    logger.info("Rule created: code=%s weight=%.2f", rule.code, rule.weight)
    return FraudRuleResponse.model_validate(rule)


# ===========================================================================
# GET  /api/v1/rules
# ===========================================================================
@router.get(
    "/",
    response_model=list[FraudRuleResponse],
    summary="List All Fraud Rules",
)
async def list_rules(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(FraudRule)
    if active_only:
        stmt = stmt.where(FraudRule.is_active.is_(True))
    stmt = stmt.order_by(FraudRule.created_at.desc())
    result = await db.execute(stmt)
    rules = list(result.scalars())
    return [FraudRuleResponse.model_validate(r) for r in rules]


# ===========================================================================
# GET  /api/v1/rules/{rule_id}
# ===========================================================================
@router.get(
    "/{rule_id}",
    response_model=FraudRuleResponse,
    summary="Get a Fraud Rule",
)
async def get_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    rule = await _fetch_rule(db, rule_id)
    return FraudRuleResponse.model_validate(rule)


# ===========================================================================
# PUT  /api/v1/rules/{rule_id}  — full replace
# ===========================================================================
@router.put(
    "/{rule_id}",
    response_model=FraudRuleResponse,
    summary="Replace a Fraud Rule",
)
async def replace_rule(
    rule_id: str,
    body: FraudRuleCreate,
    db: AsyncSession = Depends(get_db),
):
    rule = await _fetch_rule(db, rule_id)
    rule.code = body.code
    rule.name = body.name
    rule.description = body.description
    rule.weight = body.weight
    rule.condition = body.condition
    await db.commit()
    await db.refresh(rule)
    logger.info("Rule replaced: id=%s code=%s", rule_id, rule.code)
    return FraudRuleResponse.model_validate(rule)


# ===========================================================================
# PATCH /api/v1/rules/{rule_id}  — partial update
# ===========================================================================
@router.patch(
    "/{rule_id}",
    response_model=FraudRuleResponse,
    summary="Partial-Update a Fraud Rule",
)
async def patch_rule(
    rule_id: str,
    body: FraudRuleUpdate,
    db: AsyncSession = Depends(get_db),
):
    rule = await _fetch_rule(db, rule_id)

    if body.name is not None:
        rule.name = body.name
    if body.description is not None:
        rule.description = body.description
    if body.weight is not None:
        rule.weight = body.weight
    if body.condition is not None:
        rule.condition = body.condition
    if body.is_active is not None:
        rule.is_active = body.is_active

    await db.commit()
    await db.refresh(rule)
    logger.info("Rule patched: id=%s is_active=%s", rule_id, rule.is_active)
    return FraudRuleResponse.model_validate(rule)


# ===========================================================================
# DELETE /api/v1/rules/{rule_id}  — soft delete
# ===========================================================================
@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate (Soft-Delete) a Fraud Rule",
)
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    rule = await _fetch_rule(db, rule_id)
    rule.is_active = False
    await db.commit()
    logger.info("Rule soft-deleted: id=%s code=%s", rule_id, rule.code)


# ===========================================================================
# Helper
# ===========================================================================
async def _fetch_rule(db: AsyncSession, rule_id: str) -> FraudRule:
    result = await db.execute(select(FraudRule).where(FraudRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found.")
    return rule
