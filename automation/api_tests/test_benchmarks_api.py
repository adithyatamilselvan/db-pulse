"""API tests: controlled benchmark engine + the before/after optimization workflow."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mongo
API = "/api/v1/benchmarks"


def spec(orders_db, **kw):
    return {
        "database": orders_db,
        "collection": "orders",
        "filter": {"customer_id": 7, "status": "COMPLETED"},
        "warmup_iterations": 2,
        "iterations": 30,
        "concurrency": 2,
        **kw,
    }


def run_and_wait(client, headers, iid, body, wait):
    r = client.post(f"{API}/{iid}/run", headers=headers, json=body)
    assert r.status_code == 202, r.text
    bid = r.json()["benchmark"]["id"]
    return wait(
        lambda: (lambda b: b if b["status"] in ("completed", "failed", "cancelled") else None)(
            client.get(f"{API}/{bid}", headers=headers).json()["benchmark"]
        ),
        timeout=90,
    )


def test_benchmark_measures_real_latency_and_records_plan(client, admin, instance, orders_db, wait):
    b = run_and_wait(client, admin["headers"], instance["id"], spec(orders_db, label="baseline"), wait)
    r = b["result"]
    assert b["status"] == "completed" and r["successful"] == 30 and r["failed"] == 0
    lat = r["latency_ms"]
    assert 0 < lat["min"] <= lat["median"] <= lat["p95"] <= lat["p99"] <= lat["max"]
    assert lat["avg"] > 0 and r["throughput_ops"] > 0
    assert len(r["latency_samples_ms"]) == 30 and sum(r["histogram"]["counts"]) == 30
    assert r["docs_examined"] == 60000 and r["plan"]["collection_scan_detected"] is True
    assert r["docs_returned"] > 0
    assert "Results vary" in b["methodology"]
    assert b["target"] == f"{orders_db}.orders" and b["source"] == "observed"


def test_duration_mode_and_count_and_aggregate_operations(client, admin, instance, orders_db, wait):
    h, iid = admin["headers"], instance["id"]
    d = run_and_wait(client, h, iid, spec(orders_db, iterations=1, duration_s=1.0), wait)
    assert d["status"] == "completed" and d["result"]["successful"] > 3
    c = run_and_wait(client, h, iid, spec(orders_db, operation="count", iterations=5), wait)
    assert c["status"] == "completed" and c["result"]["docs_returned"] > 0
    a = run_and_wait(
        client,
        h,
        iid,
        {
            "database": orders_db,
            "collection": "orders",
            "operation": "aggregate",
            "iterations": 5,
            "warmup_iterations": 0,
            "pipeline": [
                {"$match": {"status": "PENDING"}},
                {"$group": {"_id": "$customer_id", "n": {"$sum": 1}}},
                {"$limit": 5},
            ],
        },
        wait,
    )
    assert a["status"] == "completed" and a["result"]["docs_returned"] == 5


def test_cancel_stops_a_running_benchmark(client, admin, instance, orders_db, wait):
    h, iid = admin["headers"], instance["id"]
    r = client.post(f"{API}/{iid}/run", headers=h, json=spec(orders_db, iterations=1, duration_s=30))
    bid = r.json()["benchmark"]["id"]
    wait(
        lambda: (
            client.get(f"{API}/{bid}", headers=h).json()["benchmark"].get("progress", {}).get("completed", 0)
            > 0
        ),
        timeout=20,
    )
    assert client.post(f"{API}/{bid}/cancel", headers=h).json()["cancel_requested"] is True
    b = wait(
        lambda: (lambda x: x if x["status"] != "running" and x["status"] != "queued" else None)(
            client.get(f"{API}/{bid}", headers=h).json()["benchmark"]
        ),
        timeout=30,
    )
    assert b["status"] == "cancelled" and b["result"]["cancelled"] is True


def test_errors_are_collected_not_hidden(client, admin, instance, orders_db, wait):
    # $regex on a numeric field with an invalid pattern fails server-side on every call
    b = run_and_wait(
        client,
        admin["headers"],
        instance["id"],
        spec(orders_db, filter={"status": {"$regex": "("}}, iterations=5, warmup_iterations=0),
        wait,
    )
    assert b["status"] == "failed" and b["result"]["failed"] == 5 and b["result"]["errors"]


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"iterations": 10**7}, "iterations"),
        ({"concurrency": 500}, "concurrency"),
        ({"duration_s": 99999}, "duration"),
        ({"timeout_ms": 50}, "timeout"),
        ({"iterations": 0}, None),
        ({"warmup_iterations": -1}, None),
        ({"filter": {"$where": "sleep(5000)"}}, "not allowed"),
        ({"operation": "aggregate", "pipeline": [{"$out": "stolen"}]}, "not allowed"),
    ],
)
def test_invalid_or_unsafe_configurations_rejected(client, admin, instance, orders_db, override, needle):
    r = client.post(f"{API}/{instance['id']}/run", headers=admin["headers"], json=spec(orders_db, **override))
    assert r.status_code == 422
    if needle:
        assert needle in str(r.json()["error"])


def test_high_load_requires_explicit_confirmation(client, admin, instance, orders_db, wait):
    h, iid = admin["headers"], instance["id"]
    heavy = spec(orders_db, iterations=2500, concurrency=2, warmup_iterations=0)
    r = client.post(f"{API}/{iid}/run", headers=h, json=heavy)
    assert r.status_code == 422 and r.json()["error"]["details"]["high_load"] is True
    pre = client.post(f"{API}/{iid}/validate", headers=h, json=heavy).json()
    assert pre["high_load"] is True and pre["target"] == f"{orders_db}.orders"


def test_missing_collection_is_404_and_nothing_is_stored(client, admin, instance, orders_db, internal_db):
    from bson import ObjectId

    before = internal_db.benchmarks.count_documents({"instance_id": ObjectId(instance["id"])})
    r = client.post(
        f"{API}/{instance['id']}/run",
        headers=admin["headers"],
        json={**spec(orders_db), "collection": "nope"},
    )
    assert r.status_code == 404
    assert internal_db.benchmarks.count_documents({"instance_id": ObjectId(instance["id"])}) == before


def test_baseline_index_retest_compare_report_workflow(
    client, admin, instance, orders_db, monitored_client, wait
):
    """The interview demo, as an automated test: results are whatever MongoDB actually does."""
    h, iid = admin["headers"], instance["id"]
    coll = monitored_client[orders_db].orders
    coll.drop_index("customer_id_1_status_1") if "customer_id_1_status_1" in {
        i["name"] for i in coll.list_indexes()
    } else None
    body = spec(orders_db, iterations=40, concurrency=2)

    base = run_and_wait(client, h, iid, {**body, "label": "baseline"}, wait)
    assert base["result"]["plan"]["collection_scan_detected"]

    created = client.post(
        f"/api/v1/indexes/{iid}/create",
        headers=h,
        json={
            "database": orders_db,
            "collection": "orders",
            "keys": [["customer_id", 1], ["status", 1]],
            "confirm": True,
            "acknowledge_write_cost": True,
        },
    )
    assert created.status_code == 201
    try:
        after = run_and_wait(client, h, iid, {**body, "label": "with-index"}, wait)
        ar = after["result"]
        assert ar["plan"]["index_scan_detected"] and not ar["plan"]["collection_scan_detected"]
        assert ar["plan"]["index_names"] == ["customer_id_1_status_1"]
        assert ar["docs_examined"] < base["result"]["docs_examined"]
        assert ar["docs_returned"] == base["result"]["docs_returned"]  # same query, same answer

        cmp_r = client.post(
            f"{API}/compare", headers=h, json={"before_id": base["id"], "after_id": after["id"]}
        )
        assert cmp_r.status_code == 201
        c = cmp_r.json()["comparison"]
        rows = {r["key"]: r for r in c["rows"]}
        assert rows["docs_examined"]["change_pct"] > 90
        assert rows["p95"]["before"] == base["result"]["latency_ms"]["p95"]  # real numbers, passed through
        assert c["plan"]["collscan_before"] and not c["plan"]["collscan_after"]
        assert c["verdict"] in ("IMPROVED", "UNCHANGED", "REGRESSED")  # whatever really happened is reported
        assert c["same_query"] is True and c["source"] == "observed"
        detail = client.get(f"{API}/comparisons/{c['id']}", headers=h).json()
        assert detail["before"]["label"] == "baseline" and detail["after"]["label"] == "with-index"

        # report built from the stored comparison
        rep = client.post(
            "/api/v1/reports", headers=h, json={"instance_id": iid, "period": "24h", "comparison_id": c["id"]}
        )
        assert rep.status_code == 201
        content = client.get(f"/api/v1/reports/{rep.json()['report']['id']}", headers=h).json()["report"][
            "content"
        ]
        assert content["benchmark_comparisons"][0]["verdict"] == c["verdict"]
    finally:
        client.post(
            f"/api/v1/indexes/{iid}/rollback",
            headers=h,
            json={"change_id": created.json()["change"]["id"], "confirm": True},
        )


def test_compare_validation_and_regression_alert(client, admin, instance, orders_db, wait, internal_db):
    h, iid = admin["headers"], instance["id"]
    assert (
        client.post(f"{API}/compare", headers=h, json={"before_id": "x", "after_id": "y"}).status_code == 404
    )
    fast = run_and_wait(
        client, h, iid, spec(orders_db, filter={"order_id": 5}, iterations=15, label="fast"), wait
    )
    slow = run_and_wait(
        client,
        h,
        iid,
        spec(orders_db, filter={"status": {"$ne": "x"}}, limit=5000, iterations=15, label="slow"),
        wait,
    )
    c = client.post(
        f"{API}/compare", headers=h, json={"before_id": fast["id"], "after_id": slow["id"]}
    ).json()["comparison"]
    assert c["same_query"] is False and any("different query" in x for x in c["caveats"])
    if c["verdict"] == "REGRESSED":
        assert internal_db.alerts.count_documents({"type": "BENCHMARK_REGRESSION"}) >= 1
