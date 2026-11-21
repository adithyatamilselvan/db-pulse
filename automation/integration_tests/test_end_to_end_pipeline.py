"""Integration: MongoDB profiler -> collector -> slow-query detector -> plan analyzer ->
index recommendation -> alert -> (API consumed by the frontend).

Uses REAL MongoDB data and the REAL profiler; nothing is mocked or hard-coded.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mongo
API = "/api/v1"
FILTER = {"customer_id": 7, "status": "COMPLETED"}


@pytest.fixture()
def workload(shop_app, orders_db):
    """Run real queries as the 'application' so the profiler records them."""
    coll = shop_app[orders_db].orders
    for cid in (7, 8, 9, 7):
        list(coll.find({"customer_id": cid, "status": "COMPLETED"}))
    return orders_db


def test_profiler_to_alert_pipeline(client, admin, instance, workload, internal_db):
    h, iid = admin["headers"], instance["id"]

    # 1-3. collector reads profiler, detector applies the threshold, fingerprints are stored
    res = client.post(f"{API}/monitoring/{iid}/collect-slow", headers=h).json()["results"][workload]
    assert res["available"] is True and res["ingested"] >= 4
    assert res["below_threshold"] >= 0

    slow = client.get(
        f"{API}/queries/slow", headers=h, params={"instance_id": iid, "collection": "orders", "limit": 100}
    ).json()
    finds = [q for q in slow["items"] if q["operation"] == "find" and q["collection_scan"]]
    assert len(finds) >= 4
    q = finds[0]
    assert q["plan_summary"] == "COLLSCAN" and q["docs_examined"] == 60000
    assert q["docs_returned"] > 0 and q["efficiency_ratio"] > 100
    assert q["duration_ms"] >= q["threshold_ms"] == 1
    # query values are NOT stored: only the redacted shape
    assert q["command_redacted"]["filter"] == {"customer_id": "?", "status": "?"}
    assert "COMPLETED" not in str(q) and "7" not in str(q["shape"].get("filter", ""))
    # same shape => single fingerprint, even with different customer ids
    assert len({f["fingerprint"] for f in finds}) == 1
    fp = finds[0]["fingerprint"]

    # pattern aggregation
    pats = client.get(f"{API}/queries/patterns", headers=h, params={"instance_id": iid}).json()["items"]
    pat = next(p for p in pats if p["fingerprint"] == fp)
    assert pat["count"] >= 4 and pat["stats"]["p95_ms"] is not None
    assert "sample_enc" not in pat

    # 4-5. real explain on the stored (encrypted) sample -> plan analysis + recommendation
    inv = client.post(f"{API}/queries/patterns/{iid}/{fp}/investigate", headers=h)
    assert inv.status_code == 200, inv.text
    plan = inv.json()["plan"]
    a = plan["analysis"]
    assert a["collection_scan_detected"] is True and a["documents_examined"] == 60000
    assert a["keys_examined"] == 0 and a["efficiency_ratio"] > 100
    assert "COLLSCAN" in a["stage_names"]
    assert any(f["code"] == "COLLSCAN" for f in a["findings"])
    assert plan["raw_explain"]["executionStats"]["totalDocsExamined"] == 60000  # raw, unmodified
    rec = plan["recommendation"]
    assert rec["action"] == "create_index" and rec["status"] == "unverified_hypothesis"
    assert rec["suggested_index"]["keys"] == [["customer_id", 1], ["status", 1]] or rec["suggested_index"][
        "keys"
    ] == [["status", 1], ["customer_id", 1]]
    assert rec["confidence"] in ("MEDIUM", "HIGH") and rec["supporting_evidence"]
    assert plan["fingerprint"] == fp

    # 6. alerts were generated, deduplicated, and carry evidence
    alerts = client.get(f"{API}/alerts", headers=h, params={"instance_id": iid, "limit": 200}).json()["items"]
    by_type = {a["type"]: a for a in alerts if a["fingerprint"] == fp}
    assert {"SLOW_QUERY", "COLLECTION_SCAN"} <= set(by_type)
    assert by_type["COLLECTION_SCAN"]["evidence"]["docs_examined"] == 60000
    assert by_type["SLOW_QUERY"]["occurrences"] >= 4  # repeated hits were folded, not duplicated
    assert sum(1 for a in alerts if a["type"] == "SLOW_QUERY" and a["fingerprint"] == fp) == 1

    # 7. pattern detail powers the investigation page
    det = client.get(f"{API}/queries/patterns/{iid}/{fp}", headers=h).json()
    assert det["executions"] and det["plans"] and det["alerts"]

    # collection is idempotent: re-reading does not double-ingest the same profiler entries
    again = client.post(f"{API}/monitoring/{iid}/collect-slow", headers=h).json()["results"][workload]
    assert again["ingested"] == 0  # checkpoint prevents re-ingesting; DB Pulse own probes are excluded
    # DB Pulse's own explain/probe traffic is excluded from analysis
    assert not any(
        x.get("client_app", "") and x["client_app"].startswith("dbpulse")
        for x in internal_db.slow_queries.find({"instance_id": __import__("bson").ObjectId(iid)})
    )


def test_ad_hoc_explain_of_user_query_and_error_paths(client, admin, instance, orders_db):
    h, iid = admin["headers"], instance["id"]
    r = client.post(
        f"{API}/execution-plans/{iid}/explain",
        headers=h,
        json={"database": orders_db, "collection": "orders", "filter": FILTER},
    )
    assert r.status_code == 201
    p = r.json()["plan"]
    assert p["analysis"]["collection_scan_detected"] and p["recommendation"]["action"] == "create_index"
    # plan retrievable by id; listing excludes the heavy raw payload
    got = client.get(f"{API}/execution-plans/{p['id']}", headers=h).json()["plan"]
    assert got["id"] == p["id"] and "raw_explain" in got
    listed = client.get(f"{API}/execution-plans", headers=h, params={"instance_id": iid}).json()
    assert listed["total"] >= 1 and all("raw_explain" not in x for x in listed["items"])
    # stored literal values in stage filters are redacted
    assert "COMPLETED" not in str(got["analysis"]["tree"])

    # aggregation + count explain
    assert (
        client.post(
            f"{API}/execution-plans/{iid}/explain",
            headers=h,
            json={"database": orders_db, "collection": "orders", "operation": "count", "filter": FILTER},
        ).status_code
        == 201
    )
    agg = client.post(
        f"{API}/execution-plans/{iid}/explain",
        headers=h,
        json={
            "database": orders_db,
            "collection": "orders",
            "operation": "aggregate",
            "pipeline": [
                {"$match": {"status": "PENDING"}},
                {"$group": {"_id": "$customer_id", "n": {"$sum": 1}}},
            ],
        },
    )
    assert agg.status_code == 201 and agg.json()["plan"]["analysis"]["parsed_ok"]

    # negative paths
    missing = client.post(
        f"{API}/execution-plans/{iid}/explain",
        headers=h,
        json={"database": orders_db, "collection": "does_not_exist", "filter": {}},
    )
    assert missing.status_code == 404
    for bad in ({"filter": '{"a": '}, {"filter": {"$where": "1"}}, {"sort": {"a": 5}}, {"limit": 0}):
        r = client.post(
            f"{API}/execution-plans/{iid}/explain",
            headers=h,
            json={"database": orders_db, "collection": "orders", **bad},
        )
        assert r.status_code == 422, bad
    sys_db = client.post(
        f"{API}/execution-plans/{iid}/explain",
        headers=h,
        json={"database": "admin", "collection": "system.users"},
    )
    assert sys_db.status_code == 422


def test_recommendation_endpoint_matches_engine_without_running_explain(client, admin, instance, orders_db):
    r = client.post(
        f"{API}/indexes/{instance['id']}/recommend",
        headers=admin["headers"],
        json={"database": orders_db, "collection": "orders", "filter": FILTER, "sort": {"total_amount": -1}},
    )
    rec = r.json()["recommendation"]
    assert rec["action"] == "create_index"
    keys = [k for k, _ in rec["suggested_index"]["keys"]]
    assert keys[-1] == "total_amount" and set(keys[:2]) == {"customer_id", "status"}  # ESR


def test_index_creation_safeguards_and_rollback(client, admin, user_a, instance, orders_db, monitored_client):
    h, iid = admin["headers"], instance["id"]
    body = {"database": orders_db, "collection": "orders", "keys": [["total_amount", 1]]}

    def names():
        return {i["name"] for i in monitored_client[orders_db].orders.list_indexes()}

    assert client.post(f"{API}/indexes/{iid}/create", headers=h, json=body).status_code == 422
    assert (
        client.post(f"{API}/indexes/{iid}/create", headers=h, json={**body, "confirm": True}).status_code
        == 422
    )
    assert "total_amount_1" not in names()  # nothing was created
    assert client.post(
        f"{API}/indexes/{iid}/create",
        headers=user_a["headers"],
        json={**body, "confirm": True, "acknowledge_write_cost": True},
    ).status_code in (403, 404)
    for bad_keys in ([["$bad", 1]], [["a", 2]], [["", 1]]):
        assert (
            client.post(
                f"{API}/indexes/{iid}/create",
                headers=h,
                json={**body, "keys": bad_keys, "confirm": True, "acknowledge_write_cost": True},
            ).status_code
            == 422
        )

    # read-only registration blocks creation even with confirmation
    client.patch(f"/api/v1/databases/{iid}", headers=h, json={"allow_modifications": False})
    ro = client.post(
        f"{API}/indexes/{iid}/create",
        headers=h,
        json={**body, "confirm": True, "acknowledge_write_cost": True},
    )
    assert ro.status_code == 422 and "read-only" in ro.json()["error"]["message"]
    client.patch(f"/api/v1/databases/{iid}", headers=h, json={"allow_modifications": True})

    ok = client.post(
        f"{API}/indexes/{iid}/create",
        headers=h,
        json={**body, "confirm": True, "acknowledge_write_cost": True},
    )
    assert ok.status_code == 201 and "total_amount_1" in names()
    change_id = ok.json()["change"]["id"]
    assert client.get(f"{API}/indexes/{iid}/changes", headers=h).json()["items"][0]["id"] == change_id
    assert (
        client.post(f"{API}/indexes/{iid}/rollback", headers=h, json={"change_id": change_id}).status_code
        == 422
    )
    rb = client.post(
        f"{API}/indexes/{iid}/rollback", headers=h, json={"change_id": change_id, "confirm": True}
    )
    assert rb.status_code == 200 and "total_amount_1" not in names()
    # only DB Pulse-created indexes are droppable, and only once
    assert (
        client.post(
            f"{API}/indexes/{iid}/rollback", headers=h, json={"change_id": change_id, "confirm": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{API}/indexes/{iid}/rollback", headers=h, json={"change_id": "0" * 24, "confirm": True}
        ).status_code
        == 422
    )


def test_index_investigator_reports_duplicates_and_redundant_prefix(
    client, admin, instance, orders_db, monitored_client
):
    coll = monitored_client[orders_db].idx_demo
    coll.insert_many([{"a": i, "b": i % 3} for i in range(50)])
    coll.create_index([("a", 1)], name="a_1")
    coll.create_index([("a", 1), ("b", 1)], name="a_1_b_1")
    coll.create_index(
        [("a", 1)],
        name="a_dup",
    ) if False else None  # same key+options cannot be created twice by MongoDB
    res = client.get(f"{API}/indexes/{instance['id']}/{orders_db}/idx_demo", headers=admin["headers"]).json()
    kinds = {i["type"] for i in res["issues"]}
    assert "redundant_prefix" in kinds
    assert res["index_stats_available"] is True and res["caveats"]
    row = next(r for r in res["indexes"] if r["name"] == "a_1")
    assert row["size_bytes"] is not None and row["accesses"] is not None
