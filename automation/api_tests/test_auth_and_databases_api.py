"""API tests: authentication and database (instance) management, incl. negative paths."""

from __future__ import annotations

import secrets

import pytest

from conftest import MONITORED_URI

pytestmark = pytest.mark.mongo
API = "/api/v1"


def reg(client, email=None, password="Str0ng-password-1", name="Tester"):
    return client.post(
        f"{API}/auth/register",
        json={"email": email or f"t-{secrets.token_hex(3)}@example.com", "password": password, "name": name},
    )


# --- auth ---------------------------------------------------------------------------
def test_register_login_me_roundtrip(client, admin):
    email = f"rt-{secrets.token_hex(3)}@example.com"
    r = reg(client, email)
    assert r.status_code == 201 and r.json()["user"]["role"] == "user"
    assert "password" not in r.text and "hash" not in r.text
    login = client.post(f"{API}/auth/login", json={"email": email, "password": "Str0ng-password-1"})
    assert login.status_code == 200
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get(f"{API}/auth/me", headers=h).json()["user"]["email"] == email
    assert client.post(f"{API}/auth/logout", headers=h).json() == {"ok": True}


def test_first_user_is_admin_and_later_users_are_not(admin, user_a):
    assert admin["user"]["role"] == "admin" and user_a["user"]["role"] == "user"


def test_duplicate_email_conflict(client, user_a):
    r = reg(client, user_a["user"]["email"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize("pw", ["short1", "alllettersnodigits", "1234567890123"])
def test_weak_passwords_rejected(client, pw):
    r = reg(client, password=pw)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"


def test_bad_email_rejected(client):
    assert reg(client, email="not-an-email").status_code == 422


def test_login_failures_are_uniform(client, user_a):
    wrong_pw = client.post(
        f"{API}/auth/login", json={"email": user_a["user"]["email"], "password": "nope-nope-123"}
    )
    no_user = client.post(
        f"{API}/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-123"}
    )
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["error"]["message"] == no_user.json()["error"]["message"]


def test_profile_update_and_password_change(client, user_a):
    h = user_a["headers"]
    assert (
        client.patch(f"{API}/auth/me", headers=h, json={"name": "Renamed"}).json()["user"]["name"]
        == "Renamed"
    )
    bad = client.patch(
        f"{API}/auth/me",
        headers=h,
        json={"current_password": "wrong-pass-123", "new_password": "N3w-password-77"},
    )
    assert bad.status_code == 422
    ok = client.patch(
        f"{API}/auth/me",
        headers=h,
        json={"current_password": "Str0ng-password-1", "new_password": "N3w-password-77"},
    )
    assert ok.status_code == 200
    assert (
        client.post(
            f"{API}/auth/login", json={"email": user_a["user"]["email"], "password": "N3w-password-77"}
        ).status_code
        == 200
    )


# --- database registration --------------------------------------------------------------
def test_test_endpoint_reports_version_without_echoing_uri(client, admin):
    r = client.post(f"{API}/databases/test", headers=admin["headers"], json={"uri": MONITORED_URI})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["version"] and body["latency_ms"] >= 0
    assert "uri" not in body and MONITORED_URI not in r.text


def test_register_multiple_instances_and_list(client, admin, instance):
    r = client.post(
        f"{API}/databases",
        headers=admin["headers"],
        json={"name": f"second-{secrets.token_hex(2)}", "uri": MONITORED_URI},
    )
    assert r.status_code == 201
    second = r.json()["instance"]
    assert second["allow_modifications"] is False  # safest default: read-only
    listed = client.get(f"{API}/databases", headers=admin["headers"]).json()["instances"]
    ids = {i["id"] for i in listed}
    assert instance["id"] in ids and second["id"] in ids
    assert all("uri" not in i and "uri_enc" not in i for i in listed)
    client.delete(f"{API}/databases/{second['id']}", headers=admin["headers"])


def test_duplicate_instance_name_conflicts(client, admin, instance):
    r = client.post(
        f"{API}/databases", headers=admin["headers"], json={"name": instance["name"], "uri": MONITORED_URI}
    )
    assert r.status_code == 409


def test_invalid_uri_is_rejected_without_leaking_it(client, admin):
    r = client.post(
        f"{API}/databases/test", headers=admin["headers"], json={"uri": "http://user:secretpw@host/db"}
    )
    assert r.status_code == 422 and "secretpw" not in r.text
    r2 = client.post(
        f"{API}/databases/test", headers=admin["headers"], json={"uri": "mongodb://user:secretpw@:badport/db"}
    )
    assert r2.status_code == 422 and "secretpw" not in r2.text


def test_unreachable_server_fails_gracefully(client, admin):
    r = client.post(
        f"{API}/databases/test",
        headers=admin["headers"],
        json={"uri": "mongodb://127.0.0.1:1/?directConnection=true"},
    )
    body = r.json()
    assert r.status_code == 200 and body["ok"] is False and body["error_code"] == "unreachable"
    reg_r = client.post(
        f"{API}/databases",
        headers=admin["headers"],
        json={"name": "dead", "uri": "mongodb://127.0.0.1:1/?directConnection=true"},
    )
    assert reg_r.status_code == 502 and reg_r.json()["error"]["code"] == "connection_failed"


def test_invalid_credentials_are_reported_as_auth_failure_and_not_echoed(client, admin):
    uri = MONITORED_URI.replace("mongodb://", "mongodb://baduser:badpassw0rd@")
    r = client.post(f"{API}/databases/test", headers=admin["headers"], json={"uri": uri})
    assert r.json()["ok"] is False and r.json()["error_code"] == "auth_failed"
    assert "badpassw0rd" not in r.text


def test_edit_name_and_modification_flag_then_disconnect_connect_delete(client, admin, instance):
    h, iid = admin["headers"], instance["id"]
    new = f"renamed-{secrets.token_hex(2)}"
    u = client.patch(
        f"{API}/databases/{iid}", headers=h, json={"name": new, "allow_modifications": False}
    ).json()["instance"]
    assert u["name"] == new and u["allow_modifications"] is False
    d = client.post(f"{API}/databases/{iid}/disconnect", headers=h).json()["instance"]
    assert d["status"] == "disconnected" and d["enabled"] is False
    # monitoring a disconnected instance is refused with a clear message
    assert client.post(f"/api/v1/monitoring/{iid}/collect", headers=h).status_code == 422
    c = client.post(f"{API}/databases/{iid}/connect", headers=h).json()["instance"]
    assert c["status"] == "connected" and c["enabled"] is True
    assert client.post(f"{API}/databases/{iid}/check", headers=h).json()["connection"]["ok"] is True


def test_explorer_lists_databases_collections_and_indexes(client, admin, instance, orders_db):
    h, iid = admin["headers"], instance["id"]
    dbs = client.get(f"{API}/databases/{iid}/explorer", headers=h).json()["databases"]
    assert orders_db in {d["name"] for d in dbs} and "admin" not in {d["name"] for d in dbs}
    cols = client.get(f"{API}/databases/{iid}/explorer/{orders_db}/collections", headers=h).json()[
        "collections"
    ]
    orders = next(c for c in cols if c["name"] == "orders")
    assert orders["document_count"] == 60000 and orders["stats_available"] is True
    assert not any(c["name"].startswith("system.") for c in cols)
    idx = client.get(f"{API}/databases/{iid}/explorer/{orders_db}/orders/indexes", headers=h).json()
    assert [i["name"] for i in idx["indexes"]] == ["_id_"]


def test_settings_roundtrip_and_validation(client, admin, instance):
    h, iid = admin["headers"], instance["id"]
    s = client.get(f"{API}/databases/{iid}/settings", headers=h).json()["settings"]
    s.update(
        slow_threshold_ms=250,
        collection_thresholds_ms={"orders": 75},
        severity_overrides={"COLLECTION_SCAN": "CRITICAL"},
        retention_days=7,
    )
    assert client.put(f"{API}/databases/{iid}/settings", headers=h, json=s).status_code == 200
    assert (
        client.get(f"{API}/databases/{iid}/settings", headers=h).json()["settings"]["slow_threshold_ms"]
        == 250
    )
    s["slow_threshold_ms"] = 0
    assert client.put(f"{API}/databases/{iid}/settings", headers=h, json=s).status_code == 422
    s.update(slow_threshold_ms=10, severity_overrides={"NOT_A_TYPE": "HIGH"})
    assert client.put(f"{API}/databases/{iid}/settings", headers=h, json=s).status_code == 422
    s.update(severity_overrides={}, monitoring_interval_s=1)
    assert client.put(f"{API}/databases/{iid}/settings", headers=h, json=s).status_code == 422


def test_delete_cascades_instance_data(client, admin, internal_db):
    r = client.post(
        f"{API}/databases",
        headers=admin["headers"],
        json={"name": f"tmp-{secrets.token_hex(2)}", "uri": MONITORED_URI},
    )
    iid = r.json()["instance"]["id"]
    assert client.post(f"{API}/monitoring/{iid}/collect", headers=admin["headers"]).status_code == 200
    from bson import ObjectId

    assert internal_db.metrics.count_documents({"instance_id": ObjectId(iid)}) == 1
    d = client.delete(f"{API}/databases/{iid}", headers=admin["headers"]).json()
    assert d["deleted"] is True and d["removed_records"]["metrics"] == 1
    assert internal_db.metrics.count_documents({"instance_id": ObjectId(iid)}) == 0
    assert client.get(f"{API}/databases/{iid}", headers=admin["headers"]).status_code == 404
