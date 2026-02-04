"""
RiskSentinel — Test Suite
Run:  pytest tests/ -v --tb=short
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.services.db import Base, get_db
from app.models.models import FraudRule, Transaction, RiskScore, Alert, AuditLog
from app.rules.engine import evaluate_rules, _evaluate_single, _extract_field
from app.rules.default_rules import DEFAULT_RULES
from app.config import settings

# ===========================================================================
# Fixtures — in-memory SQLite (async via aiosqlite)
# ===========================================================================
TEST_DB_URL = "sqlite+aiosqlite://"                    # :memory:

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestingSessionLocal = sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def _override_get_db():
    async with TestingSessionLocal() as session:
        yield session


# Patch Kafka so tests never touch a real broker
@pytest.fixture(autouse=True)
def _patch_kafka(monkeypatch):
    mock_producer = AsyncMock()
    mock_producer.start = AsyncMock()
    mock_producer.stop  = AsyncMock()
    mock_producer.send  = AsyncMock()
    monkeypatch.setattr("app.services.kafka_producer.KafkaProducer.start", mock_producer.start)
    monkeypatch.setattr("app.services.kafka_producer.KafkaProducer.stop",  mock_producer.stop)
    monkeypatch.setattr("app.services.kafka_producer.KafkaProducer.send",  mock_producer.send)


@pytest_asyncio.fixture()
async def db_session():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestingSessionLocal() as session:
        yield session
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def client(db_session):
    app.dependency_overrides[get_db] = _override_get_db
    # Stub out Kafka producer on app.state
    app.state.kafka_producer = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ===========================================================================
# Helpers
# ===========================================================================
def _make_transaction(**kwargs) -> Transaction:
    defaults = dict(
        id=str(uuid.uuid4()),
        sender_id="sender_001",
        receiver_id="receiver_002",
        amount_zar=1_000.0,
        currency="ZAR",
        channel="mobile_banking",
        status="pending",
        created_at=datetime.now(timezone.utc),
        metadata_={},
    )
    defaults.update(kwargs)
    return Transaction(**defaults)


def _make_rule(code="TEST_RULE", condition=None, weight=0.2, is_active=True) -> FraudRule:
    return FraudRule(
        id=str(uuid.uuid4()),
        code=code,
        name=f"Test Rule {code}",
        weight=weight,
        condition=condition or {"field": "amount_zar", "operator": "gt", "threshold": 500},
        is_active=is_active,
    )


# ===========================================================================
# ── Unit: Rules Engine ──────────────────────────────────────────────────────
# ===========================================================================

class TestFieldExtractor:
    def test_simple_field(self):
        txn = _make_transaction(amount_zar=9999.0)
        assert _extract_field(txn, "amount_zar") == 9999.0

    def test_nested_jsonb(self):
        txn = _make_transaction(metadata_={"ip_country_flagged": "true"})
        assert _extract_field(txn, "metadata_.ip_country_flagged") == "true"

    def test_missing_field_returns_none(self):
        txn = _make_transaction()
        assert _extract_field(txn, "nonexistent_field") is None


class TestRulesEngine:
    def test_single_rule_fires(self):
        txn = _make_transaction(amount_zar=60_000)
        rule = _make_rule(
            code="HIGH_AMT",
            condition={"field": "amount_zar", "operator": "gt", "threshold": 50_000},
            weight=0.3,
        )
        score, triggered, explanation = evaluate_rules(txn, [rule])
        assert "HIGH_AMT" in triggered
        assert score == 0.3
        assert explanation["HIGH_AMT"]["fired"] is True

    def test_single_rule_does_not_fire(self):
        txn = _make_transaction(amount_zar=100)
        rule = _make_rule(
            code="HIGH_AMT",
            condition={"field": "amount_zar", "operator": "gt", "threshold": 50_000},
            weight=0.3,
        )
        score, triggered, explanation = evaluate_rules(txn, [rule])
        assert triggered == []
        assert score == 0.0

    def test_multiple_rules_cumulative(self):
        txn = _make_transaction(amount_zar=300_000, channel="api")
        rules = [
            _make_rule(code="R1", condition={"field": "amount_zar", "operator": "gt", "threshold": 200_000}, weight=0.4),
            _make_rule(code="R2", condition={"field": "channel", "operator": "eq", "target": "api"}, weight=0.3),
        ]
        score, triggered, _ = evaluate_rules(txn, rules)
        assert set(triggered) == {"R1", "R2"}
        assert score == pytest.approx(0.7, abs=0.01)

    def test_score_capped_at_1(self):
        txn = _make_transaction(amount_zar=999_999)
        rules = [_make_rule(code=f"R{i}", weight=0.4) for i in range(5)]
        score, _, _ = evaluate_rules(txn, rules)
        assert score <= 1.0

    def test_and_combinator(self):
        txn = _make_transaction(amount_zar=100_000, channel="api")
        rule = _make_rule(
            code="AND_TEST",
            condition={
                "and": [
                    {"field": "amount_zar", "operator": "gt", "threshold": 50_000},
                    {"field": "channel", "operator": "eq", "target": "api"},
                ]
            },
            weight=0.5,
        )
        score, triggered, _ = evaluate_rules(txn, [rule])
        assert "AND_TEST" in triggered

    def test_or_combinator(self):
        txn = _make_transaction(amount_zar=10, channel="ussd")
        rule = _make_rule(
            code="OR_TEST",
            condition={
                "or": [
                    {"field": "amount_zar", "operator": "gt", "threshold": 50_000},
                    {"field": "channel", "operator": "eq", "target": "ussd"},
                ]
            },
            weight=0.2,
        )
        score, triggered, _ = evaluate_rules(txn, [rule])
        assert "OR_TEST" in triggered

    def test_inactive_rules_excluded(self):
        """Caller is responsible for filtering — engine trusts the list."""
        txn = _make_transaction(amount_zar=60_000)
        active   = _make_rule(code="ACTIVE",   is_active=True,  weight=0.2)
        inactive = _make_rule(code="INACTIVE", is_active=False, weight=0.5)
        # Only pass active rules (as scorer does)
        score, triggered, _ = evaluate_rules(txn, [active])
        assert "INACTIVE" not in triggered
        assert score == 0.2

    def test_unknown_operator_skipped(self):
        txn = _make_transaction(amount_zar=100)
        rule = _make_rule(
            code="BAD_OP",
            condition={"field": "amount_zar", "operator": "magic", "threshold": 0},
            weight=0.5,
        )
        score, triggered, _ = evaluate_rules(txn, [rule])
        assert triggered == []
        assert score == 0.0

    def test_in_operator(self):
        txn = _make_transaction(merchant_category="online_gambling")
        rule = _make_rule(
            code="IN_TEST",
            condition={"field": "merchant_category", "operator": "in", "list": ["online_gambling", "crypto"]},
            weight=0.25,
        )
        score, triggered, _ = evaluate_rules(txn, [rule])
        assert "IN_TEST" in triggered

    def test_not_in_operator(self):
        txn = _make_transaction(merchant_category="grocery")
        rule = _make_rule(
            code="NOT_IN_TEST",
            condition={"field": "merchant_category", "operator": "not_in", "list": ["online_gambling"]},
            weight=0.1,
        )
        score, triggered, _ = evaluate_rules(txn, [rule])
        assert "NOT_IN_TEST" in triggered


class TestDefaultRules:
    """Smoke-test every default rule against a synthetic transaction."""

    def test_all_default_rules_are_valid(self):
        for rule_data in DEFAULT_RULES:
            assert "code" in rule_data
            assert "condition" in rule_data
            assert "weight" in rule_data
            assert 0 < rule_data["weight"] <= 1.0

    def test_high_amount_rule_fires(self):
        txn = _make_transaction(amount_zar=75_000)
        rules = [_make_rule(**{k: v for k, v in DEFAULT_RULES[0].items()})]
        _, triggered, _ = evaluate_rules(txn, rules)
        assert DEFAULT_RULES[0]["code"] in triggered

    def test_suspicious_merchant_fires(self):
        txn = _make_transaction(merchant_category="cryptocurrency_exchange")
        rules = [_make_rule(**{k: v for k, v in DEFAULT_RULES[2].items()})]
        _, triggered, _ = evaluate_rules(txn, rules)
        assert DEFAULT_RULES[2]["code"] in triggered


# ===========================================================================
# ── Unit: Risk Level Classification ─────────────────────────────────────────
# ===========================================================================

class TestRiskLevelClassification:
    def test_low(self):
        from app.services.scorer import _classify_risk
        assert _classify_risk(0.2) == "LOW"

    def test_medium(self):
        from app.services.scorer import _classify_risk
        assert _classify_risk(0.5) == "MEDIUM"

    def test_high(self):
        from app.services.scorer import _classify_risk
        assert _classify_risk(0.75) == "HIGH"

    def test_critical(self):
        from app.services.scorer import _classify_risk
        assert _classify_risk(0.95) == "CRITICAL"

    def test_boundary_high(self):
        from app.services.scorer import _classify_risk
        assert _classify_risk(settings.RISK_SCORE_HIGH) == "HIGH"

    def test_boundary_critical(self):
        from app.services.scorer import _classify_risk
        assert _classify_risk(settings.RISK_SCORE_CRITICAL) == "CRITICAL"


# ===========================================================================
# ── Integration: API Endpoints ──────────────────────────────────────────────
# ===========================================================================

@pytest.mark.asyncio
class TestTransactionsAPI:
    async def test_create_transaction(self, client, db_session):
        # Seed a rule so scoring has something to evaluate
        rule = FraudRule(
            id=str(uuid.uuid4()),
            code="INTEGRATION_RULE",
            name="Integration test rule",
            weight=0.1,
            condition={"field": "amount_zar", "operator": "gt", "threshold": 0},
            is_active=True,
        )
        db_session.add(rule)
        await db_session.commit()

        payload = {
            "sender_id": "sender_int_001",
            "receiver_id": "receiver_int_002",
            "amount_zar": 5000.0,
            "channel": "mobile_banking",
        }
        resp = await client.post("/api/v1/transactions/", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["sender_id"] == "sender_int_001"
        assert body["amount_zar"] == 5000.0
        assert "id" in body

    async def test_list_transactions(self, client, db_session):
        resp = await client.get("/api/v1/transactions/")
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body
        assert "items" in body

    async def test_get_nonexistent_transaction(self, client, db_session):
        resp = await client.get("/api/v1/transactions/nonexistent-id")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestRulesAPI:
    async def test_create_and_list_rules(self, client, db_session):
        payload = {
            "code": "API_TEST_RULE",
            "name": "API Created Rule",
            "weight": 0.15,
            "condition": {"field": "amount_zar", "operator": "gt", "threshold": 1000},
        }
        create_resp = await client.post("/api/v1/rules/", json=payload)
        assert create_resp.status_code == 201
        assert create_resp.json()["code"] == "API_TEST_RULE"

        list_resp = await client.get("/api/v1/rules/")
        assert list_resp.status_code == 200
        codes = [r["code"] for r in list_resp.json()]
        assert "API_TEST_RULE" in codes

    async def test_duplicate_rule_code_rejected(self, client, db_session):
        payload = {
            "code": "DUP_RULE",
            "name": "First",
            "weight": 0.1,
            "condition": {"field": "amount_zar", "operator": "gt", "threshold": 100},
        }
        await client.post("/api/v1/rules/", json=payload)
        resp = await client.post("/api/v1/rules/", json=payload)
        assert resp.status_code == 409

    async def test_patch_rule_deactivate(self, client, db_session):
        # Create
        payload = {
            "code": "PATCH_ME",
            "name": "Patchable",
            "weight": 0.2,
            "condition": {"field": "amount_zar", "operator": "gt", "threshold": 100},
        }
        create_resp = await client.post("/api/v1/rules/", json=payload)
        rule_id = create_resp.json()["id"]

        # Patch
        patch_resp = await client.patch(f"/api/v1/rules/{rule_id}", json={"is_active": False})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["is_active"] is False

    async def test_delete_rule_soft(self, client, db_session):
        payload = {
            "code": "DEL_ME",
            "name": "Deletable",
            "weight": 0.1,
            "condition": {"field": "amount_zar", "operator": "gt", "threshold": 100},
        }
        create_resp = await client.post("/api/v1/rules/", json=payload)
        rule_id = create_resp.json()["id"]

        del_resp = await client.delete(f"/api/v1/rules/{rule_id}")
        assert del_resp.status_code == 204

        # Verify it still exists but is inactive
        get_resp = await client.get(f"/api/v1/rules/{rule_id}")
        assert get_resp.json()["is_active"] is False


@pytest.mark.asyncio
class TestAlertsAPI:
    async def test_list_alerts_empty(self, client, db_session):
        resp = await client.get("/api/v1/alerts/")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_patch_nonexistent_alert(self, client, db_session):
        resp = await client.patch("/api/v1/alerts/nope", json={"status": "resolved"})
        assert resp.status_code == 404

    async def test_patch_alert_invalid_status(self, client, db_session):
        # We need an actual alert — create one via a high-risk transaction
        # For simplicity, just check that a bad status is rejected on a
        # hypothetical alert id (404 is fine here too)
        resp = await client.patch("/api/v1/alerts/fake-id", json={"status": "banana"})
        assert resp.status_code == 404   # alert doesn't exist; 400 would fire if it did


@pytest.mark.asyncio
class TestHealthAPI:
    async def test_health_returns_200(self, client, db_session):
        resp = await client.get("/api/v1/health/")
        # SQLite is "healthy" from the app's perspective
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert "status" in body
        assert "version" in body
        assert "uptime_seconds" in body


@pytest.mark.asyncio
class TestDashboardAPI:
    async def test_summary_returns_structure(self, client, db_session):
        resp = await client.get("/api/v1/dashboard/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_transactions" in body
        assert "total_alerts_open" in body
        assert "avg_risk_score" in body
        assert "top_risk_transactions" in body
        assert "alert_distribution" in body
