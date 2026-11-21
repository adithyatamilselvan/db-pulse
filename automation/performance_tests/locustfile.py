"""Locust load test for the DB Pulse API.

Scenarios: concurrent dashboard requests, slow-query / pattern / alert history retrieval,
multiple independent login sessions, and a light benchmark workload.

Setup (once, at test start): registers/logs in the account given by LOCUST_EMAIL/LOCUST_PASSWORD,
and creates the demo instance (REAL demo collection + SYNTHETIC history) if none exists.

IMPORTANT: DB Pulse rate-limits per client IP (default 240 req/min, 10 auth/min). All simulated
users share one IP, so run the *target backend* with RATE_LIMIT_PER_MINUTE and
AUTH_RATE_LIMIT_PER_MINUTE raised (see docs/TESTING_STRATEGY.md). Never point this at a
production deployment.

    locust -f automation/performance_tests/locustfile.py --headless -u 20 -r 5 -t 45s \
        --host http://127.0.0.1:18081 --csv test-results/locust
"""

from __future__ import annotations

import os
import time

import httpx
from locust import HttpUser, between, events, task

EMAIL = os.environ.get("LOCUST_EMAIL", "perf@example.com")
PASSWORD = os.environ.get("LOCUST_PASSWORD", "Perf-password-12345")
DEMO_ORDERS = int(os.environ.get("LOCUST_DEMO_ORDERS", "5000"))
STATE: dict[str, str] = {}


@events.test_start.add_listener
def prepare(environment, **_kw):
    base = environment.host.rstrip("/") + "/api/v1"
    with httpx.Client(base_url=base, timeout=120) as c:
        r = c.post("/auth/register", json={"email": EMAIL, "password": PASSWORD, "name": "Perf"})
        if r.status_code == 409:
            r = c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        r.raise_for_status()
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        insts = c.get("/databases").json()["instances"]
        demo = next((i for i in insts if i["is_demo"]), None)
        if demo is None:
            if r.json()["user"]["role"] != "admin":
                raise RuntimeError("First registered user must be admin to create the demo instance")
            s = c.post("/demo/setup", json={"orders": max(DEMO_ORDERS, 1000), "confirm": True}, timeout=300)
            s.raise_for_status()
            demo = s.json()["instance"]
        STATE["instance_id"] = demo["id"]
        patterns = c.get("/queries/patterns", params={"instance_id": demo["id"]}).json()["items"]
        STATE["fingerprint"] = patterns[0]["fingerprint"] if patterns else ""


class DbPulseUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        for _ in range(5):  # tolerate 429 from the auth limiter while many users start at once
            with self.client.post(
                "/api/v1/auth/login",
                json={"email": EMAIL, "password": PASSWORD},
                name="POST /auth/login",
                catch_response=True,
            ) as r:
                if r.status_code == 200:
                    self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
                    r.success()
                    return
                if r.status_code == 429:
                    r.success()  # expected throttling, retry
                    time.sleep(float(r.headers.get("Retry-After", "1")))
                    continue
                r.failure(f"login failed {r.status_code}")
                return

    @task(5)
    def dashboard(self) -> None:
        self.client.get(
            "/api/v1/monitoring/dashboard", params={"period": "24h"}, name="GET /monitoring/dashboard"
        )

    @task(4)
    def slow_queries(self) -> None:
        self.client.get(
            "/api/v1/queries/slow",
            params={"instance_id": STATE["instance_id"], "limit": 50, "period": "24h"},
            name="GET /queries/slow",
        )

    @task(3)
    def query_history(self) -> None:
        if STATE.get("fingerprint"):
            self.client.get(
                f"/api/v1/queries/patterns/{STATE['instance_id']}/{STATE['fingerprint']}",
                name="GET /queries/patterns/{id}/{fp}",
            )
        self.client.get(
            "/api/v1/queries/patterns",
            params={"instance_id": STATE["instance_id"]},
            name="GET /queries/patterns",
        )

    @task(2)
    def alerts(self) -> None:
        self.client.get(
            "/api/v1/alerts", params={"instance_id": STATE["instance_id"], "limit": 100}, name="GET /alerts"
        )

    @task(1)
    def explorer(self) -> None:
        self.client.get(
            f"/api/v1/databases/{STATE['instance_id']}/explorer", name="GET /databases/{id}/explorer"
        )

    @task(1)
    def benchmark_workload(self) -> None:
        """Start a small read-only benchmark and poll it to completion."""
        body = {
            "database": os.environ.get("LOCUST_DEMO_DB", "dbpulse_demo"),
            "collection": "orders",
            "filter": {"customer_id": 1001, "status": "COMPLETED"},
            "iterations": 20,
            "warmup_iterations": 1,
            "concurrency": 1,
            "label": "locust",
        }
        with self.client.post(
            f"/api/v1/benchmarks/{STATE['instance_id']}/run",
            json=body,
            name="POST /benchmarks/{id}/run",
            catch_response=True,
        ) as r:
            if r.status_code != 202:
                r.failure(f"unexpected {r.status_code}: {r.text[:120]}")
                return
            bid = r.json()["benchmark"]["id"]
        for _ in range(40):
            res = self.client.get(f"/api/v1/benchmarks/{bid}", name="GET /benchmarks/{id}")
            if res.status_code == 200 and res.json()["benchmark"]["status"] in (
                "completed",
                "failed",
                "cancelled",
            ):
                return
            time.sleep(0.25)
