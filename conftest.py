"""Shared pytest configuration and fixtures for the whole repository.

Environment is configured BEFORE the app is imported so tests use an isolated internal
database and never touch a user's real data. Monitored databases are created with unique
names and dropped afterwards. Tests only ever write to databases prefixed ``dbpulse_test_``.
"""

from __future__ import annotations

import os
import secrets
import uuid

from cryptography.fernet import Fernet

RUN_ID = uuid.uuid4().hex[:8]
INTERNAL_URI = os.environ.get("TEST_INTERNAL_MONGO_URI", "mongodb://127.0.0.1:27117")
MONITORED_URI = os.environ.get("TEST_MONITORED_MONGO_URI", "mongodb://127.0.0.1:27118")

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "INTERNAL_MONGO_URI": INTERNAL_URI,
        "INTERNAL_DB_NAME": f"dbpulse_test_internal_{RUN_ID}",
        "DEMO_MONGO_URI": MONITORED_URI,
        "DEMO_DB_NAME": f"dbpulse_test_{RUN_ID}_demo",
        "ENABLE_INPROCESS_SCHEDULER": "false",
        "RATE_LIMIT_PER_MINUTE": "100000",
        "AUTH_RATE_LIMIT_PER_MINUTE": "100000",
        "AI_PROVIDER": "local",
        "COHERE_API_KEY": "",
        "COHERE_MODEL": "",
        "MONGO_SERVER_SELECTION_TIMEOUT_MS": "1500",
        "MONGO_CONNECT_TIMEOUT_MS": "1500",
    }
)
os.environ.pop("DATABASE_URL", None)

import pytest
from pymongo import MongoClient


def _reachable(uri: str) -> bool:
    try:
        MongoClient(uri, serverSelectionTimeoutMS=1500).admin.command("ping")
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def mongo_available() -> bool:
    ok = _reachable(INTERNAL_URI) and _reachable(MONITORED_URI)
    if not ok:
        pytest.skip("Test MongoDB instances are not running (see scripts/dev_mongo.ps1)")
    return ok


@pytest.fixture(scope="session")
def app(mongo_available):
    from app.main import create_app

    return create_app()


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    from app.database import get_client

    get_client().drop_database(os.environ["INTERNAL_DB_NAME"])


@pytest.fixture(scope="session")
def internal_db(client):
    from app.database import get_internal_db

    return get_internal_db()


def _register(client, name: str):
    email = f"{name}-{secrets.token_hex(3)}@example.com"
    r = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "Str0ng-password-1", "name": name}
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]


@pytest.fixture(scope="session")
def admin(client):
    """First user registered in the fresh test DB => administrator."""
    headers, user = _register(client, "admin")
    assert user["role"] == "admin"
    return {"headers": headers, "user": user}


@pytest.fixture()
def user_a(client, admin):
    headers, user = _register(client, "usera")
    assert user["role"] == "user"
    return {"headers": headers, "user": user}


@pytest.fixture()
def user_b(client, admin):
    headers, user = _register(client, "userb")
    return {"headers": headers, "user": user}


@pytest.fixture(scope="session")
def monitored_client(mongo_available):
    c = MongoClient(MONITORED_URI, serverSelectionTimeoutMS=3000, appname="dbpulse-test-driver")
    yield c
    for name in c.list_database_names():
        if name.startswith(f"dbpulse_test_{RUN_ID}"):
            c.drop_database(name)
    c.close()


@pytest.fixture(scope="session")
def orders_db(monitored_client):
    """REAL data in a unique monitored database; profiler on (level 2) for integration tests."""
    import random

    name = f"dbpulse_test_{RUN_ID}_shop"
    db = monitored_client[name]
    rng = random.Random(1234)
    docs = [
        {
            "order_id": i,
            "customer_id": rng.randint(1, 600),
            "status": rng.choice(["COMPLETED", "PENDING", "CANCELLED"]),
            "total_amount": round(rng.uniform(1, 999), 2),
        }
        for i in range(60000)
    ]
    db.orders.insert_many(docs)
    db.command("profile", 2, slowms=0)
    return name


@pytest.fixture()
def instance(client, admin, orders_db):
    """The monitored test MongoDB registered through the real API (modifications allowed)."""
    name = f"inst-{secrets.token_hex(3)}"
    r = client.post(
        "/api/v1/databases",
        headers=admin["headers"],
        json={"name": name, "uri": MONITORED_URI, "allow_modifications": True},
    )
    assert r.status_code == 201, r.text
    inst = r.json()["instance"]
    settings = {
        **inst["settings"],
        "monitored_databases": [orders_db],
        "slow_threshold_ms": 1,
        "alert_cooldown_s": 900,
    }
    r = client.put(f"/api/v1/databases/{inst['id']}/settings", headers=admin["headers"], json=settings)
    assert r.status_code == 200, r.text
    yield inst
    client.delete(f"/api/v1/databases/{inst['id']}", headers=admin["headers"])


@pytest.fixture(scope="session")
def shop_app(mongo_available):
    """A client posing as the monitored application (appName NOT prefixed 'dbpulse', so the
    profiler entries it generates are collected rather than filtered as DB Pulse's own)."""
    c = MongoClient(MONITORED_URI, serverSelectionTimeoutMS=3000, appname="shop-app")
    yield c
    c.close()


def wait_for(predicate, timeout: float = 60.0, interval: float = 0.25):
    import time

    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


@pytest.fixture()
def wait():
    return wait_for
