"""API tests: metrics collection/history, profiler controls, retention."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.mongo
API = "/api/v1/monitoring"


def test_metrics_snapshot_contains_real_server_data(client, admin, instance):
    r = client.post(f"{API}/{instance['id']}/collect", headers=admin["headers"])
    assert r.status_code == 200
    snap = r.json()["snapshot"]
    sv = snap["server"]
    assert isinstance(sv["connections_current"], int) and sv["connections_current"] >= 1
    assert sv["version"].startswith(("7", "8", "6")) and sv["uptime_s"] > 0
    assert sv["rates"] is None  # first snapshot: no previous sample, so unavailable (not zero)
    assert sv["opcounters"] is not None and "query" in sv["opcounters"]
    assert any(d["name"] for d in snap["databases"])


def test_second_snapshot_has_rates_and_history_returns_both(client, admin, instance):
    h, iid = admin["headers"], instance["id"]
    client.post(f"{API}/{iid}/collect", headers=h)
    time.sleep(1.1)
    second = client.post(f"{API}/{iid}/collect", headers=h).json()["snapshot"]
    rates = second["server"]["rates"]
    assert rates is not None and all(v is None or v >= 0 for v in rates.values())
    pts = client.get(f"{API}/{iid}/metrics", headers=h, params={"period": "15m"}).json()["points"]
    assert len(pts) >= 2 and pts[0]["ts"] < pts[-1]["ts"]
    assert all("_op_latencies_raw" not in p["server"] for p in pts)


@pytest.mark.parametrize(
    "params",
    [
        {"period": "bogus"},
        {"period": "custom"},
        {"start": "2026-02-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
    ],
)
def test_invalid_periods_rejected(client, admin, instance, params):
    r = client.get(f"{API}/{instance['id']}/metrics", headers=admin["headers"], params=params)
    assert r.status_code == 422


def test_custom_range_works(client, admin, instance):
    r = client.get(
        f"{API}/{instance['id']}/metrics",
        headers=admin["headers"],
        params={"start": "2020-01-01T00:00:00Z", "end": "2020-01-02T00:00:00Z"},
    )
    assert r.status_code == 200 and r.json()["points"] == []


def test_profiler_status_is_read_only_and_reports_config(client, admin, instance, orders_db):
    r = client.get(
        f"{API}/{instance['id']}/profiler", headers=admin["headers"], params={"database": orders_db}
    )
    st = r.json()["status"]
    assert st["available"] is True and st["level"] == 2 and st["slowms"] == 0
    assert "overhead" in r.json()["warning"]


def test_profiler_change_requires_confirmation_admin_and_instance_optin(
    client, admin, user_a, instance, monitored_client
):
    import conftest

    db_name = f"dbpulse_test_{conftest.RUN_ID}_prof"
    monitored_client[db_name].items.insert_one({"x": 1})
    h, iid = admin["headers"], instance["id"]
    body = {"database": db_name, "level": 1, "slowms": 5}
    # no confirmation => refused and nothing changed
    assert client.post(f"{API}/{iid}/profiler", headers=h, json=body).status_code == 422
    assert monitored_client[db_name].command("profile", -1)["was"] == 0
    # standard users can never change it
    assert client.post(
        f"{API}/{iid}/profiler", headers=user_a["headers"], json={**body, "confirm": True}
    ).status_code in (403, 404)
    # confirmed => applied, previous config recorded, then restorable
    ok = client.post(f"{API}/{iid}/profiler", headers=h, json={**body, "confirm": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["previous"]["level"] == 0 and ok.json()["current"]["level"] == 1
    assert monitored_client[db_name].command("profile", -1)["was"] == 1
    assert (
        client.post(
            f"{API}/{iid}/profiler/restore", headers=h, json={"change_id": ok.json()["change_id"]}
        ).status_code
        == 422
    )
    rest = client.post(
        f"{API}/{iid}/profiler/restore",
        headers=h,
        json={"change_id": ok.json()["change_id"], "confirm": True},
    )
    assert rest.status_code == 200 and rest.json()["restored"]["level"] == 0
    assert monitored_client[db_name].command("profile", -1)["was"] == 0


def test_profiler_change_refused_on_readonly_registered_instance(client, admin, instance, orders_db):
    h, iid = admin["headers"], instance["id"]
    client.patch(f"/api/v1/databases/{iid}", headers=h, json={"allow_modifications": False})
    r = client.post(
        f"{API}/{iid}/profiler", headers=h, json={"database": orders_db, "level": 0, "confirm": True}
    )
    assert r.status_code == 422 and "read-only" in r.json()["error"]["message"]
    client.patch(f"/api/v1/databases/{iid}", headers=h, json={"allow_modifications": True})


def test_missing_profiler_is_reported_not_raised(client, admin, instance, monitored_client):
    import conftest

    db_name = f"dbpulse_test_{conftest.RUN_ID}_noprofiler"
    monitored_client[db_name].items.insert_one({"x": 1})
    h, iid = admin["headers"], instance["id"]
    s = client.get(f"/api/v1/databases/{iid}/settings", headers=h).json()["settings"]
    s["monitored_databases"] = [db_name]
    client.put(f"/api/v1/databases/{iid}/settings", headers=h, json=s)
    res = client.post(f"{API}/{iid}/collect-slow", headers=h).json()["results"][db_name]
    assert res["available"] is False and "Profiling is off" in res["reason"]


def test_dashboard_shape_and_unavailable_semantics(client, admin, instance):
    r = client.get(f"{API}/dashboard", headers=admin["headers"], params={"period": "24h"})
    assert r.status_code == 200
    d = r.json()
    for k in (
        "instances",
        "slow_queries",
        "collection_scans",
        "index_usage",
        "active_alerts",
        "recent_investigations",
        "trend",
    ):
        assert k in d
    assert d["slow_queries"]["note"]
    if d["slow_queries"]["total"] == 0:
        assert d["slow_queries"]["avg_ms"] is None and d["slow_queries"]["p95_ms"] is None


def test_cleanup_applies_retention(client, admin, instance, internal_db):
    from datetime import UTC, datetime, timedelta

    from bson import ObjectId

    iid = ObjectId(instance["id"])
    internal_db.metrics.insert_one(
        {
            "instance_id": iid,
            "owner_id": ObjectId(),
            "ts": datetime.now(UTC) - timedelta(days=400),
            "source": "observed",
            "server": {},
            "databases": [],
        }
    )
    removed = client.post(f"{API}/{instance['id']}/cleanup", headers=admin["headers"]).json()["removed"]
    assert removed["metrics"] >= 1
    assert (
        internal_db.metrics.count_documents(
            {"instance_id": iid, "ts": {"$lt": datetime.now(UTC) - timedelta(days=300)}}
        )
        == 0
    )
