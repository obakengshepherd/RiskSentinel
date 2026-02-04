"""
RiskSentinel — ML Training Script
Generates synthetic South-African-style transaction data, trains an
IsolationForest, evaluates it, and serialises the model.

Usage
-----
    python -m ml.train

Output
------
    ml/models/anomaly_model.pkl
    ml/models/training_report.json
"""

import json
import pickle
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MODEL_DIR = Path("ml/models")
MODEL_PATH = MODEL_DIR / "anomaly_model.pkl"
REPORT_PATH = MODEL_DIR / "training_report.json"

MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------
CHANNELS = ["api", "mobile_banking", "pos", "ussd"]

def _generate_normal_transactions(n: int = 8_000) -> np.ndarray:
    """Normal South-African retail transaction patterns."""
    amounts   = np.random.lognormal(mean=7.5, sigma=1.2, size=n).clip(10, 45_000)
    channels  = np.array([CHANNELS.index(random.choice(CHANNELS)) for _ in range(n)])
    hours     = np.random.choice(range(24), size=n, p=_sa_hourly_distribution())
    is_intl   = np.zeros(n)                            # local by default
    return np.column_stack([amounts, channels, hours, is_intl])


def _generate_fraudulent_transactions(n: int = 500) -> np.ndarray:
    """Synthetic fraud patterns: high amounts, odd hours, international flags."""
    amounts   = np.random.uniform(60_000, 500_000, size=n)
    channels  = np.full(n, CHANNELS.index("api"))      # API-heavy fraud
    hours     = np.random.choice([0, 1, 2, 3, 22, 23], size=n)
    is_intl   = np.ones(n)                             # flagged as international
    return np.column_stack([amounts, channels, hours, is_intl])


def _sa_hourly_distribution() -> list:
    """
    Rough probability per hour that mirrors South-African banking activity.
    Peak 08–20, trough 01–05.
    """
    raw = [0.5, 0.3, 0.2, 0.2, 0.2, 0.3, 0.8, 1.2,
           1.8, 2.0, 2.0, 1.9, 1.8, 1.7, 1.6, 1.5,
           1.5, 1.6, 1.7, 1.5, 1.2, 0.9, 0.7, 0.6]
    total = sum(raw)
    return [p / total for p in raw]


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------
def main():
    print("Generating synthetic data …")
    normal = _generate_normal_transactions(8_000)
    fraud  = _generate_fraudulent_transactions(500)

    # Labels: 1 = normal, -1 = fraud  (sklearn convention)
    X = np.vstack([normal, fraud])
    y = np.concatenate([np.ones(len(normal)), -np.ones(len(fraud))])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    print(f"Training on {len(X_train)} samples …")
    model = IsolationForest(
        n_estimators=200,
        contamination=0.06,       # ~6 % fraud rate
        max_samples="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train)

    # Evaluate
    y_pred = model.predict(X_test)                    # 1 or -1
    report = classification_report(y_test, y_pred, target_names=["fraud", "normal"], output_dict=True)
    print("\n", classification_report(y_test, y_pred, target_names=["fraud", "normal"]))

    # Persist
    with open(MODEL_PATH, "wb") as fh:
        pickle.dump(model, fh)
    print(f"Model saved → {MODEL_PATH}")

    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"Report saved → {REPORT_PATH}")


if __name__ == "__main__":
    main()
