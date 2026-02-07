"""
RiskSentinel — Load Testing Script
Run:  locust -f locustfile.py --host=http://localhost:8000
"""

from locust import HttpUser, task, between
import json
import uuid


class RiskSentinelLoadTest(HttpUser):
    """Simulates payment transactions hitting the RiskSentinel API."""

    wait_time = between(0.5, 2.0)

    def on_start(self):
        """Initialize test data."""
        self.transaction_count = 0
        self.base_headers = {
            "Content-Type": "application/json"
        }

    @task(4)
    def submit_normal_transaction(self):
        """Submit a low-risk transaction (80% of traffic)."""
        payload = {
            "sender_id": f"sender_{uuid.uuid4().hex[:8]}",
            "receiver_id": f"receiver_{uuid.uuid4().hex[:8]}",
            "amount_zar": 5_000.00,
            "currency": "ZAR",
            "channel": "mobile_banking",
            "merchant_category": "retail",
            "ip_address": "192.168.1.1",
            "device_fingerprint": f"fingerprint_{uuid.uuid4().hex[:8]}",
        }

        with self.client.post(
            "/api/v1/transactions",
            json=payload,
            headers=self.base_headers,
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                response.success()
                self.transaction_count += 1
            else:
                response.failure(f"Got {response.status_code}: {response.text}")

    @task(1)
    def submit_high_risk_transaction(self):
        """Submit a high-risk suspicious transaction (20% of traffic)."""
        payload = {
            "sender_id": f"fraud_sender_{uuid.uuid4().hex[:8]}",
            "receiver_id": f"receiver_{uuid.uuid4().hex[:8]}",
            "amount_zar": 200_000.00,  # Triggers RULE_CRITICAL_AMOUNT
            "currency": "ZAR",
            "channel": "api",
            "merchant_category": "cryptocurrency_exchange",
            "metadata": {
                "ip_country_flagged": "true",
                "repeat_receiver": True,
            },
        }

        with self.client.post(
            "/api/v1/transactions",
            json=payload,
            headers=self.base_headers,
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                response.success()
                data = response.json()
                # Verify high-risk flagging
                if data.get("risk_level") not in ("HIGH", "CRITICAL"):
                    response.failure(
                        f"Expected HIGH/CRITICAL risk, got {data.get('risk_level')}"
                    )
                self.transaction_count += 1
            else:
                response.failure(f"Got {response.status_code}: {response.text}")

    @task(2)
    def list_transactions(self):
        """Retrieve list of transactions with pagination."""
        with self.client.get(
            "/api/v1/transactions?page=1&page_size=25",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got {response.status_code}")

    @task(1)
    def get_alerts(self):
        """Check open alerts."""
        with self.client.get(
            "/api/v1/alerts?status_filter=open",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got {response.status_code}")

    @task(1)
    def health_check(self):
        """Perform health check."""
        with self.client.get(
            "/api/v1/health",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "healthy":
                    response.success()
                else:
                    response.failure(f"System status: {data.get('status')}")
            else:
                response.failure(f"Got {response.status_code}")

    @task(1)
    def get_dashboard(self):
        """Retrieve dashboard KPIs."""
        with self.client.get(
            "/api/v1/dashboard/summary",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got {response.status_code}")
