"""
RiskSentinel — Fraud Rules Engine
Evaluates a list of dynamic, database-backed rules against an incoming transaction.
Each rule is a JSON object with an 'operator' and parameters; the engine is fully
extensible — adding a new operator only requires registering a function in OPERATORS.
"""

import logging
from typing import Any, Dict, List, Tuple

from app.models.models import FraudRule, Transaction

logger = logging.getLogger("risksentinel.rules")


# ===========================================================================
# Operator registry
# Each operator receives (transaction_value, rule_params) and returns bool.
# ===========================================================================

def _op_gt(value: Any, params: Dict) -> bool:
    """Greater-than check."""
    return float(value) > float(params["threshold"])


def _op_gte(value: Any, params: Dict) -> bool:
    return float(value) >= float(params["threshold"])


def _op_lt(value: Any, params: Dict) -> bool:
    return float(value) < float(params["threshold"])


def _op_lte(value: Any, params: Dict) -> bool:
    return float(value) <= float(params["threshold"])


def _op_eq(value: Any, params: Dict) -> bool:
    return str(value) == str(params["target"])


def _op_neq(value: Any, params: Dict) -> bool:
    return str(value) != str(params["target"])


def _op_in(value: Any, params: Dict) -> bool:
    """Check if value is inside an allowed / disallowed list."""
    return str(value) in [str(v) for v in params["list"]]


def _op_not_in(value: Any, params: Dict) -> bool:
    return str(value) not in [str(v) for v in params["list"]]


def _op_contains(value: Any, params: Dict) -> bool:
    """String-contains."""
    return str(params["substring"]).lower() in str(value).lower()


OPERATORS: Dict[str, Any] = {
    "gt":         _op_gt,
    "gte":        _op_gte,
    "lt":         _op_lt,
    "lte":        _op_lte,
    "eq":         _op_eq,
    "neq":        _op_neq,
    "in":         _op_in,
    "not_in":     _op_not_in,
    "contains":   _op_contains,
}


# ===========================================================================
# Field extractor — pulls nested values from the Transaction ORM object
# ===========================================================================

def _extract_field(txn: Transaction, field_path: str) -> Any:
    """
    Supports dot-notation for nested JSONB columns.
    e.g. 'geolocation.lat'  →  txn.geolocation["lat"]
    """
    parts = field_path.split(".")
    value: Any = txn

    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
        if value is None:
            return None
    return value


# ===========================================================================
# Single-rule evaluator
# ===========================================================================
# Expected rule condition shape:
# {
#     "field": "amount_zar",           ← dotted path on Transaction
#     "operator": "gt",                ← key in OPERATORS
#     "threshold": 25000,              ← extra params fed to operator
#     "and": [ ... ],                  ← optional AND group
#     "or":  [ ... ]                   ← optional OR group
# }

def _evaluate_single(txn: Transaction, condition: Dict[str, Any]) -> bool:
    """Recursively evaluate one condition node."""
    # --- AND / OR combinators ---------------------------------------------------
    if "and" in condition:
        return all(_evaluate_single(txn, sub) for sub in condition["and"])
    if "or" in condition:
        return any(_evaluate_single(txn, sub) for sub in condition["or"])

    # --- Leaf comparison --------------------------------------------------------
    field = condition.get("field")
    operator = condition.get("operator")

    if not field or not operator:
        logger.warning("Malformed rule condition (missing field/operator): %s", condition)
        return False

    op_fn = OPERATORS.get(operator)
    if op_fn is None:
        logger.warning("Unknown operator '%s' — rule skipped.", operator)
        return False

    value = _extract_field(txn, field)
    if value is None:
        logger.debug("Field '%s' is None on transaction — rule not triggered.", field)
        return False

    try:
        return op_fn(value, condition)
    except (TypeError, ValueError, KeyError) as exc:
        logger.warning("Rule evaluation error for field='%s': %s", field, exc)
        return False


# ===========================================================================
# Public API
# ===========================================================================

def evaluate_rules(
    transaction: Transaction,
    active_rules: List[FraudRule],
) -> Tuple[float, List[str], Dict[str, Any]]:
    """
    Evaluate every active rule against *transaction*.

    Returns
    -------
    rule_score      : float  – weighted sum of all triggered rules, capped at 1.0
    triggered_codes : list   – codes of rules that fired
    explanation     : dict   – { rule_code: { "fired": bool, "weight": float, "reason": str } }
    """
    total_weight = 0.0
    triggered: List[str] = []
    explanation: Dict[str, Any] = {}

    for rule in active_rules:
        fired = _evaluate_single(transaction, rule.condition)
        explanation[rule.code] = {
            "fired": fired,
            "weight": rule.weight,
            "name": rule.name,
        }
        if fired:
            triggered.append(rule.code)
            total_weight += rule.weight
            logger.info(
                "Rule '%s' TRIGGERED for txn=%s (weight=%.2f)",
                rule.code, transaction.id, rule.weight,
            )

    # Normalise to [0, 1]
    rule_score = min(total_weight, 1.0)
    return rule_score, triggered, explanation
