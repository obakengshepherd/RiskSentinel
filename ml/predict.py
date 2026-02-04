"""
RiskSentinel — ML Prediction Bridge

Loads a persisted scikit-learn IsolationForest (or any model with a
predict / decision_function interface) and translates its output into
a 0–1 anomaly score that the scorer can blend into the composite.

────────────────────────────────────────────────────────────────
How to swap in a different model
────────────────────────────────────────────────────────────────
1. Train your model (see train.py).
2. pickle.dump  it   →  ml/models/anomaly_model.pkl
3. If the new model's output shape differs, adjust _normalize().

The module is imported lazily by app/services/scorer.py so it never
blocks startup if scikit-learn is not installed.
"""

import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import settings
from app.models.models import Transaction

logger = logging.getLogger("risksentinel.ml")

# ---------------------------------------------------------------------------
# Model singleton — loaded once, cached for the lifetime of the process.
# ---------------------------------------------------------------------------
_MODEL = None
_MODEL_PATH = Path(settings.ML_MODEL_PATH)


def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    if not _MODEL_PATH.exists():
        logger.warning("Model file not found at %s — ML scoring disabled.", _MODEL_PATH)
        return None
    with open(_MODEL_PATH, "rb") as fh:
        _MODEL = pickle.load(fh)
    logger.info("ML model loaded from %s", _MODEL_PATH)
    return _MODEL


# ---------------------------------------------------------------------------
# Feature extraction  (must match the feature set used during training)
# ---------------------------------------------------------------------------
def _extract_features(txn: Transaction) -> np.ndarray:
    """
    Minimal feature vector.  Extend to match your training pipeline.

    Features
    --------
    0  amount_zar
    1  channel_encoded   (ordinal: api=0, mobile=1, pos=2, ussd=3)
    2  hour_of_day       (0-23, from created_at UTC)
    3  is_international  (1 if metadata flag set, else 0)
    """
    CHANNEL_MAP = {"api": 0, "mobile_banking": 1, "pos": 2, "ussd": 3}

    amount = txn.amount_zar or 0.0
    channel = CHANNEL_MAP.get(txn.channel, -1)
    hour = txn.created_at.hour if txn.created_at else 0
    is_intl = 1 if (txn.metadata_ or {}).get("ip_country_flagged") == "true" else 0

    return np.array([[amount, channel, hour, is_intl]], dtype=np.float64)


# ---------------------------------------------------------------------------
# Public API  (called by scorer.py via `await predict_score(txn)`)
# ---------------------------------------------------------------------------
async def predict_score(transaction: Transaction) -> Optional[float]:
    """
    Returns an anomaly score in [0, 1] or None if the model is unavailable.

    IsolationForest.decision_function returns negative scores for anomalies.
    We invert and normalise to [0, 1].
    """
    model = _load_model()
    if model is None:
        return None

    features = _extract_features(transaction)

    try:
        # decision_function: higher = more normal
        raw_score = model.decision_function(features)[0]
        score = _normalize(raw_score)
        logger.debug("ML raw_score=%.4f normalised=%.4f txn=%s", raw_score, score, transaction.id)
        return score
    except Exception as exc:
        logger.error("ML inference error: %s", exc)
        return None


def _normalize(raw: float) -> float:
    """
    Map IsolationForest decision_function output (roughly -0.5 … +0.5)
    into [0, 1] where 1 = most anomalous.
    """
    # Invert (anomalies are negative) and clamp
    inverted = -raw
    return float(np.clip((inverted + 0.5) / 1.0, 0.0, 1.0))
