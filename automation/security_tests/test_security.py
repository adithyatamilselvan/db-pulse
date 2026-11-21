"""Security tests: hashing, tokens, authn/authz enforcement, secret handling, validation,
rate limiting, headers, CORS and error handling."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from bson import ObjectId

pytestmark = pytest.mark.mongo
API = "/api/v1"
PUBLIC = {
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/ready"),
}


def test_passwords_are_argon2_hashed_and_salted(client, internal_db):
    pw = "Same-password-123"
    emails = [f"h{i}-{secrets.token_hex(3)}@example.com" for i in range(2)]
    for e in emails:
        assert (
            client.post(f"{API}/auth/register", json={"email": e, "password": pw, "name": "H"}).status_code
            == 201
        )
    hashes = [internal_db.users.find_one({"email": e})["password_hash"] for e in emails]
    assert all(h.startswith("$argon2id$") for h in hashes) and hashes[0] != hashes[1]
    assert pw not in "".join(hashes)
    from app.core.security import verify_password

    assert verify_password(pw, hashes[0]) and not verify_password("wrong", hashes[0])
    assert verify_password(pw, "not-a-hash") is False


def test_every_protected_endpoint_rejects_unauthenticated_requests(client, app):
    checked = 0
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if (method.upper(), path) in PUBLIC:
                continue
            url = path.replace("{instance_id}", "0" * 24).replace("{", "x").replace("}", "")
            url = url.replace("/xdb_name", "/db").replace("/xcollection", "/c")
            r = client.request(method.upper(), url, json={} if method != "get" else None)
            assert r.status_code == 401, f"{method.upper()} {path} -> {r.status_code}"
            checked += 1
    assert checked > 40


@pytest.mark.parametrize(
    "make",
    [
        lambda s, uid: jwt.encode(
            {"sub": uid, "role": "admin", "exp": datetime.now(UTC) + timedelta(hours=1)},
            "wrong-key-wrong-key-wrong-key-123456",
            algorithm="HS256",
        ),
        lambda s, uid: jwt.encode(
            {"sub": uid, "role": "admin", "exp": datetime.now(UTC) - timedelta(seconds=5)},
            s,
            algorithm="HS256",
        ),
        lambda s, uid: jwt.encode({"sub": uid, "role": "admin"}, None, algorithm="none"),
        lambda s, uid: jwt.encode(
            {"sub": "not-an-objectid", "exp": datetime.now(UTC) + timedelta(hours=1)}, s, algorithm="HS256"
        ),
        lambda s, uid: jwt.encode(
            {"sub": str(ObjectId()), "exp": datetime.now(UTC) + timedelta(hours=1)}, s, algorithm="HS256"
        ),
        lambda s, uid: "garbage.token.value",
    ],
)
def test_forged_expired_and_unknown_tokens_are_rejected(client, admin, make):
    from app.config import get_settings

    token = make(get_settings().secret_key, admin["user"]["id"])
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_expired_token_message_and_wrong_scheme(client, admin):
    from app.core.security import create_access_token

    t = create_access_token(admin["user"]["id"], "admin", expires_minutes=-1)
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"]
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Basic {t}"}).status_code == 401


def test_role_claim_in_token_cannot_escalate_privileges(client, user_a):
    from app.core.security import create_access_token

    forged = create_access_token(user_a["user"]["id"], "admin")  # role claim says admin, DB says user
    r = client.post(
        f"{API}/demo/setup",
        headers={"Authorization": f"Bearer {forged}"},
        json={"orders": 1000, "confirm": True},
    )
    assert r.status_code == 403


def test_users_cannot_reach_each_others_instances_or_data(client, admin, user_a, instance):
    iid, h = instance["id"], user_a["headers"]
    probes = [
        ("get", f"/databases/{iid}"),
        ("delete", f"/databases/{iid}"),
        ("post", f"/databases/{iid}/check"),
        ("get", f"/databases/{iid}/explorer"),
        ("get", f"/databases/{iid}/settings"),
        ("post", f"/monitoring/{iid}/collect"),
        ("get", f"/monitoring/{iid}/metrics"),
        ("get", f"/queries/slow?instance_id={iid}"),
        ("get", f"/alerts?instance_id={iid}"),
        ("get", f"/indexes/{iid}/db/c"),
        ("get", f"/benchmarks?instance_id={iid}"),
    ]
    for method, path in probes:
        r = getattr(client, method)(API + path, headers=h)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"
    ex = client.post(
        f"{API}/execution-plans/{iid}/explain", headers=h, json={"database": "d", "collection": "c"}
    )
    assert ex.status_code == 404
    assert client.get(f"{API}/databases", headers=h).json()["instances"] == []
    # the owner still sees it
    assert client.get(f"{API}/databases/{iid}", headers=admin["headers"]).status_code == 200


def test_admin_only_operations_forbidden_for_standard_users(client, user_a, instance):
    h = user_a["headers"]
    for path, body in [
        ("/demo/setup", {"confirm": True}),
        ("/demo/reset", {"instance_id": "x", "confirm": True}),
    ]:
        assert client.post(API + path, headers=h, json=body).status_code == 403


def test_connection_strings_are_never_exposed_or_logged(client, admin, internal_db, caplog):
    secret = "Sup3rS3cretPw"
    uri = f"mongodb://svc:{secret}@127.0.0.1:1/?directConnection=true"
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"{API}/databases/test", headers=admin["headers"], json={"uri": uri})
        r2 = client.post(f"{API}/databases", headers=admin["headers"], json={"name": "leaky", "uri": uri})
    assert secret not in r.text and secret not in r2.text
    assert secret not in caplog.text
    from app.main import _ScrubFilter

    rec = logging.LogRecord("x", logging.INFO, "f", 1, "connecting %s", (uri,), None)
    _ScrubFilter().filter(rec)
    assert secret not in rec.getMessage()
    assert not internal_db.instances.find_one({"name": "leaky"})


def test_api_responses_never_contain_encrypted_or_plain_credentials(client, admin, instance):
    from conftest import MONITORED_URI

    for path in (f"/databases/{instance['id']}", "/databases"):
        text = client.get(API + path, headers=admin["headers"]).text
        assert "uri_enc" not in text and MONITORED_URI not in text and "password_hash" not in text


def test_input_validation_blocks_injection_shaped_and_unknown_input(client, admin):
    assert (
        client.post(f"{API}/auth/login", json={"email": {"$ne": ""}, "password": {"$ne": ""}}).status_code
        == 422
    )
    assert (
        client.post(f"{API}/auth/login", json={"email": "a@b.co", "password": "x" * 5000}).status_code == 422
    )
    assert (
        client.post(
            f"{API}/auth/register",
            json={"email": "a@b.co", "password": "Str0ng-password-1", "name": "n", "role": "admin"},
        ).status_code
        == 422
    )
    r = client.post(
        f"{API}/databases/test", headers=admin["headers"], json={"uri": "mongodb://" + "a" * 3000}
    )
    assert r.status_code == 422
    assert client.get(f"{API}/alerts", headers=admin["headers"], params={"limit": 100000}).status_code == 422
    assert (
        client.get(f"{API}/queries/slow", headers=admin["headers"], params={"search": "(" * 200}).status_code
        == 200
    )  # regex-escaped


def test_rate_limiting_throttles_login_attempts(client, monkeypatch):
    from app.core import ratelimit

    class S:
        auth_rate_limit_per_minute = 3
        rate_limit_per_minute = 100000

    monkeypatch.setattr(ratelimit, "get_settings", lambda: S())
    ratelimit.limiter.reset()
    codes = [
        client.post(
            f"{API}/auth/login", json={"email": "x@example.com", "password": "bad-password-1"}
        ).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    r = client.post(f"{API}/auth/login", json={"email": "x@example.com", "password": "bad-password-1"})
    assert int(r.headers["Retry-After"]) >= 1 and r.json()["error"]["code"] == "rate_limited"
    ratelimit.limiter.reset()


def test_security_headers_and_cors_policy(client):
    r = client.get(f"{API}/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    allowed = client.get(f"{API}/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    evil = client.get(f"{API}/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_unhandled_errors_do_not_leak_internals(app):
    from app.main import create_app
    from fastapi.testclient import TestClient

    a = create_app()

    @a.get("/boom")
    def boom():
        raise RuntimeError("secret connection mongodb://u:pw@h/ failed at /srv/app/x.py")

    r = TestClient(a, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "pw@h" not in r.text and "x.py" not in r.text and "Traceback" not in r.text


def test_config_validation_fails_fast():
    from app.config import Settings
    from pydantic import ValidationError

    base = dict(secret_key="x" * 40, encryption_key="not-a-fernet-key", internal_mongo_uri="mongodb://h")
    with pytest.raises(ValidationError):
        Settings(**base)
    from cryptography.fernet import Fernet

    with pytest.raises(ValidationError):
        Settings(**{**base, "encryption_key": Fernet.generate_key().decode(), "secret_key": "short"})
    with pytest.raises(ValidationError):
        Settings(
            **{
                **base,
                "encryption_key": Fernet.generate_key().decode(),
                "environment": "production",
                "cors_origins": "*",
            }
        )


def test_encryption_roundtrip_and_tamper_detection():
    from app.core.encryption import decrypt_secret, encrypt_secret
    from app.core.exceptions import AppError

    t = encrypt_secret("mongodb://u:p@h")
    assert "mongodb" not in t and decrypt_secret(t) == "mongodb://u:p@h"
    assert encrypt_secret("mongodb://u:p@h") != t  # fresh IV each time
    with pytest.raises(AppError):
        decrypt_secret(t[:-4] + "AAAA")


def test_benchmark_and_explain_cannot_run_writes_or_js(client, admin, instance, orders_db):
    h, iid = admin["headers"], instance["id"]
    for payload in (
        {"filter": {"$where": "this.a"}},
        {"operation": "aggregate", "pipeline": [{"$merge": {"into": "x"}}]},
        {
            "operation": "aggregate",
            "pipeline": [
                {"$group": {"_id": None, "f": {"$function": {"body": "x", "args": [], "lang": "js"}}}}
            ],
        },
        {"operation": "insert"},
    ):
        base = {"database": orders_db, "collection": "orders"}
        assert (
            client.post(f"{API}/benchmarks/{iid}/run", headers=h, json={**base, **payload}).status_code == 422
        )
        assert (
            client.post(
                f"{API}/execution-plans/{iid}/explain", headers=h, json={**base, **payload}
            ).status_code
            == 422
        )
