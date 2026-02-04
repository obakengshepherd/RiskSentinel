"""
RiskSentinel — Default Fraud Rules (seed data)
These are inserted at first-run if the fraud_rules table is empty.
Designed for South African payment ecosystems (ZAR amounts, common channels).
"""

# Each dict maps directly onto FraudRule columns.
# 'condition' is the JSON tree consumed by rules/engine.py

DEFAULT_RULES = [
    # ---------------------------------------------------------------
    # 1. High-value single transaction
    # ---------------------------------------------------------------
    {
        "code": "RULE_HIGH_AMOUNT",
        "name": "High-Value Transaction",
        "description": "Single transaction exceeds ZAR 50 000 — uncommon for retail.",
        "weight": 0.25,
        "condition": {
            "field": "amount_zar",
            "operator": "gt",
            "threshold": 50_000,
        },
    },
    # ---------------------------------------------------------------
    # 2. Very high-value (critical threshold)
    # ---------------------------------------------------------------
    {
        "code": "RULE_CRITICAL_AMOUNT",
        "name": "Critical-Value Transaction",
        "description": "Single transaction exceeds ZAR 200 000.",
        "weight": 0.45,
        "condition": {
            "field": "amount_zar",
            "operator": "gt",
            "threshold": 200_000,
        },
    },
    # ---------------------------------------------------------------
    # 3. Suspicious merchant category
    # ---------------------------------------------------------------
    {
        "code": "RULE_SUSPICIOUS_MERCHANT",
        "name": "Suspicious Merchant Category",
        "description": "Transaction to a high-risk merchant category.",
        "weight": 0.20,
        "condition": {
            "field": "merchant_category",
            "operator": "in",
            "list": [
                "cryptocurrency_exchange",
                "online_gambling",
                "adult_entertainment",
                "prepaid_cards",
                "money_transfer_unlicensed",
            ],
        },
    },
    # ---------------------------------------------------------------
    # 4. Unusual channel (API with no device fingerprint)
    # ---------------------------------------------------------------
    {
        "code": "RULE_API_NO_FINGERPRINT",
        "name": "API Channel — No Device Fingerprint",
        "description": "API transaction submitted without a device fingerprint is suspicious.",
        "weight": 0.15,
        "condition": {
            "and": [
                {"field": "channel", "operator": "eq", "target": "api"},
                {"field": "device_fingerprint", "operator": "eq", "target": ""},
            ],
        },
    },
    # ---------------------------------------------------------------
    # 5. International-looking IP (placeholder — real geo-lookup
    #    would be a service; this checks for a known internal sentinel)
    # ---------------------------------------------------------------
    {
        "code": "RULE_FOREIGN_IP_FLAG",
        "name": "Foreign IP Flag",
        "description": "IP address is flagged as non-South-African by upstream enrichment.",
        "weight": 0.18,
        "condition": {
            "field": "metadata.ip_country_flagged",
            "operator": "eq",
            "target": "true",
        },
    },
    # ---------------------------------------------------------------
    # 6. Duplicate receiver in short window (handled by velocity,
    #    but rule adds a static flag for the same receiver + sender pair
    #    appearing in metadata)
    # ---------------------------------------------------------------
    {
        "code": "RULE_REPEAT_RECEIVER",
        "name": "Repeat Receiver (metadata flag)",
        "description": "Upstream enrichment flagged this sender→receiver pair as repeated.",
        "weight": 0.15,
        "condition": {
            "field": "metadata.repeat_receiver",
            "operator": "eq",
            "target": "true",
        },
    },
    # ---------------------------------------------------------------
    # 7. Zero-amount probe transaction
    # ---------------------------------------------------------------
    {
        "code": "RULE_ZERO_AMOUNT",
        "name": "Zero-Amount Probe",
        "description": "Transactions with ZAR 0.00 are often card-validation probes.",
        "weight": 0.30,
        "condition": {
            "field": "amount_zar",
            "operator": "lte",
            "threshold": 0,
        },
    },
]
