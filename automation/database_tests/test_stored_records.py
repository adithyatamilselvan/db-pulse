"""Database tests: what DB Pulse actually persists in its internal MongoDB, its integrity and
its behaviour under profiler permission failures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId, json_util
from pymongo import errors as pe

pytestmark = pytest.mark.mongo
API = "/api/v1"


def test_internal_indexes_exist_including_uniqueness_guards(internal_db):
    def idx(coll):
        return {i["name"]: i for i in internal_db[coll].list_indexes()}

    assert idx("users")["email_1"]["unique"] is True
    assert any(i.get("unique") for n, i in idx("slow_queries").items() if "dedupe_key" in n)
    assert any(i.get("unique") for n, i in idx("query_patterns").items() if "fingerprint" in n)
    assert any("instance_id" in n and "ts" in n for n in idx("metrics"))
    assert any("instance_id" in n for n in idx("alerts"))


def test_instance_record_stores_encrypted_credentials_only(internal_db, instance):
    from app.core.encryption import decrypt_secret

    from conftest import MONITORED_URI

    doc = internal_db.instances.find_one({"_id": ObjectId(instance["id"])})
    assert doc["uri_enc"] != MONITORED_URI and MONITORED_URI not in json_util.dumps(doc)
    assert decrypt_secret(doc["uri_enc"]) == MONITORED_URI
    assert doc["settings"]["slow_threshold_ms"] == 1 and doc["owner_id"]
    assert set(doc) >= {"name", "status", "allow_modifications", "enabled", "created_at", "settings"}


def test_slow_query_alert_plan_and_benchmark_records_are_complete_and_consistent(
    client, admin, instance, shop_app, orders_db, internal_db, wait
):
    h, iid = admin["headers"], instance["id"]
    list(shop_app[orders_db].orders.find({"customer_id": 21, "status": "COMPLETED"}))
    client.post(f"{API}/monitoring/{iid}/collect-slow", headers=h)
    client.post(f"{API}/monitoring/{iid}/collect", headers=h)
    fp = internal_db.slow_queries.find_one({"instance_id": ObjectId(iid), "collection": "orders"})[
        "fingerprint"
    ]
    client.post(f"{API}/queries/patterns/{iid}/{fp}/investigate", headers=h)
    b = client.post(
        f"{API}/benchmarks/{iid}/run",
        headers=h,
        json={
            "database": orders_db,
            "collection": "orders",
            "filter": {"order_id": 3},
            "iterations": 10,
            "warmup_iterations": 0,
        },
    ).json()["benchmark"]
    wait(
        lambda: internal_db.benchmarks.find_one({"_id": ObjectId(b["id"]), "status": "completed"}), timeout=30
    )

    oid = ObjectId(iid)
    sq = internal_db.slow_queries.find_one({"instance_id": oid, "fingerprint": fp})
    assert set(sq) >= {
        "database",
        "collection",
        "operation",
        "ts",
        "duration_ms",
        "fingerprint",
        "shape",
        "command_redacted",
        "plan_summary",
        "docs_examined",
        "keys_examined",
        "docs_returned",
        "severity",
        "threshold_ms",
        "source",
        "dedupe_key",
    }
    assert sq["source"] == "observed" and isinstance(sq["ts"], datetime)
    assert "21" not in json_util.dumps(sq["command_redacted"])  # no literal values at rest

    pat = internal_db.query_patterns.find_one({"instance_id": oid, "fingerprint": fp})
    assert pat["count"] == internal_db.slow_queries.count_documents({"instance_id": oid, "fingerprint": fp})
    assert pat["sample_enc"] and "COMPLETED" not in pat["sample_enc"]  # encrypted at rest

    plan = internal_db.execution_plans.find_one({"instance_id": oid, "fingerprint": fp})
    assert (
        plan["analysis"]["collection_scan_detected"]
        and plan["recommendation"]["status"] == "unverified_hypothesis"
    )
    from app.core.encryption import decrypt_secret

    raw = json_util.loads(decrypt_secret(plan["raw_explain_enc"]))
    assert raw["executionStats"]["totalDocsExamined"] == plan["analysis"]["documents_examined"]
    assert "raw_explain" not in plan  # never stored in plaintext

    bench = internal_db.benchmarks.find_one({"_id": ObjectId(b["id"])})
    assert bench["status"] == "completed" and bench["result"]["successful"] == 10
    assert bench["started_at"] <= bench["finished_at"] and bench["config"]["iterations"] == 10

    m = list(internal_db.metrics.find({"instance_id": oid}).sort("ts", 1))
    assert m and all(x["source"] == "observed" for x in m)
    assert [x["ts"] for x in m] == sorted(x["ts"] for x in m)

    alert = internal_db.alerts.find_one({"instance_id": oid, "fingerprint": fp, "type": "SLOW_QUERY"})
    assert alert["dedupe_key"].startswith("SLOW_QUERY|") and alert["evidence"]["duration_ms"] >= 1

    # referential consistency: no record points at a missing instance / owner
    valid_instances = {i["_id"] for i in internal_db.instances.find({}, {"_id": 1})}
    for coll in ("slow_queries", "alerts", "execution_plans", "benchmarks", "metrics", "query_patterns"):
        orphans = internal_db[coll].count_documents({"instance_id": {"$nin": list(valid_instances)}})
        assert orphans == 0, coll
    owners = {u["_id"] for u in internal_db.users.find({}, {"_id": 1})}
    assert internal_db.instances.count_documents({"owner_id": {"$nin": list(owners)}}) == 0


def test_ingest_is_idempotent_and_applies_thresholds(internal_db, instance):
    from app.services import slow_query_service as sqs

    inst = internal_db.instances.find_one({"_id": ObjectId(instance["id"])})
    inst["settings"].update(
        slow_threshold_ms=100,
        collection_thresholds_ms={"hot": 5},
        operation_thresholds_ms={"aggregate": 1000},
    )
    now = datetime.now(UTC)

    def entry(coll, ms, op="query", cmd=None, ts=None):
        return {
            "op": op,
            "ns": f"shopx.{coll}",
            "ts": ts or now,
            "millis": ms,
            "command": cmd or {"find": coll, "filter": {"a": 1}},
            "planSummary": "COLLSCAN",
            "docsExamined": 5000,
            "nreturned": 1,
            "keysExamined": 0,
        }

    entries = [
        entry("cold", 50),
        entry("cold", 150, ts=now + timedelta(seconds=1)),
        entry("hot", 10, ts=now + timedelta(seconds=2)),
        entry(
            "cold",
            900,
            op="command",
            cmd={"aggregate": "cold", "pipeline": [{"$match": {}}]},
            ts=now + timedelta(seconds=3),
        ),
    ]
    r1 = sqs.ingest_entries(internal_db, inst, entries)
    assert r1["ingested"] == 2 and r1["below_threshold"] == 2  # 50ms<100, aggregate 900<1000
    r2 = sqs.ingest_entries(internal_db, inst, entries)
    assert r2["ingested"] == 0 and r2["duplicates"] == 2
    assert internal_db.slow_queries.count_documents({"instance_id": inst["_id"], "database": "shopx"}) == 2


def test_alert_deduplication_and_cooldown(internal_db, instance):
    from app.services import alert_service as al

    inst = internal_db.instances.find_one({"_id": ObjectId(instance["id"])})
    inst["settings"]["alert_cooldown_s"] = 900
    kw = dict(
        instance=inst,
        alert_type="CONNECTION_FAILURE",
        severity="HIGH",
        title="t",
        message="m",
        database="dbx",
        collection="cx",
        fingerprint="fp-dedupe",
    )
    a1, created1 = al.raise_alert(internal_db, **kw)
    a2, created2 = al.raise_alert(internal_db, **kw)
    assert created1 and not created2 and a2["_id"] == a1["_id"]
    assert internal_db.alerts.find_one({"_id": a1["_id"]})["occurrences"] == 2
    # acknowledged alerts still suppress duplicates; different fingerprint does not
    al.acknowledge(internal_db, str(a1["_id"]), ObjectId())
    assert al.raise_alert(internal_db, **kw)[1] is False
    assert al.raise_alert(internal_db, **{**kw, "fingerprint": "other"})[1] is True
    # resolved + cooldown elapsed => a fresh alert is created
    al.resolve(internal_db, str(a1["_id"]), ObjectId())
    internal_db.alerts.update_one(
        {"_id": a1["_id"]}, {"$set": {"last_seen": datetime.now(UTC) - timedelta(hours=2)}}
    )
    assert al.raise_alert(internal_db, **kw)[1] is True
    # resolved but inside cooldown => still suppressed
    inst["settings"]["alert_cooldown_s"] = 900
    kw2 = {**kw, "fingerprint": "fp-cooldown"}
    b1, _ = al.raise_alert(internal_db, **kw2)
    al.resolve(internal_db, str(b1["_id"]), ObjectId())
    assert al.raise_alert(internal_db, **kw2)[1] is False
    with pytest.raises(Exception):
        al.raise_alert(internal_db, **{**kw, "alert_type": "BOGUS"})


def test_retention_removes_only_expired_rows(internal_db, instance):
    from app.services import monitoring_service as ms

    inst = internal_db.instances.find_one({"_id": ObjectId(instance["id"])})
    inst["settings"]["retention_days"] = 7
    old, fresh = datetime.now(UTC) - timedelta(days=10), datetime.now(UTC)
    for ts in (old, fresh):
        internal_db.metrics.insert_one(
            {
                "instance_id": inst["_id"],
                "owner_id": inst["owner_id"],
                "ts": ts,
                "source": "retention-test",
                "server": {},
                "databases": [],
            }
        )
    removed = ms.apply_retention(internal_db, inst)
    assert removed["metrics"] >= 1
    left = list(internal_db.metrics.find({"instance_id": inst["_id"], "source": "retention-test"}))
    assert len(left) == 1 and left[0]["ts"].replace(tzinfo=UTC) >= datetime.now(UTC) - timedelta(minutes=1)


# --- permission / unsupported-environment handling (no auth-enabled mongod needed) ---------
class _DeniedDb:
    def command(self, *a, **k):
        raise pe.OperationFailure("not authorized on db to execute command", code=13)

    def list_collection_names(self, **k):
        return []


class _DeniedClient:
    def __getitem__(self, name):
        return _DeniedDb()


def test_missing_profiler_permissions_are_reported_gracefully():
    from app.services import profiler_service as ps

    st = ps.get_profiler_status(_DeniedClient(), "d")
    assert st["available"] is False and st["reason_code"] == "not_authorized"
    read = ps.read_profile_entries(_DeniedClient(), "d", since=None)
    assert read["available"] is False and read["entries"] == [] and "not authorized" in read["reason"]
    from app.core.exceptions import UnsupportedOperation

    with pytest.raises(UnsupportedOperation):
        ps.set_profiling(_DeniedClient(), "d", level=1, slowms=10, confirmed=True)


def test_explain_failures_surface_as_unsupported_not_500():
    from app.core.exceptions import UnsupportedOperation
    from app.services import explain_service as es

    class C:
        def __getitem__(self, n):
            class D:
                def list_collection_names(self, **k):
                    return ["c"]

                def command(self, *a, **k):
                    raise pe.OperationFailure("explain not supported", code=115)

            return D()

    with pytest.raises(UnsupportedOperation):
        es.run_explain(C(), {"operation": "find", "database": "d", "collection": "c", "filter": {}})


def test_monitoring_reports_unavailable_metrics_as_none_not_zero(internal_db, instance):
    from app.services import monitoring_service as ms

    class NoStatus:
        class admin:
            @staticmethod
            def command(*a, **k):
                raise pe.OperationFailure("not authorized", code=13)

        def list_database_names(self):
            return []

        def __getitem__(self, name):
            return _DeniedDb()

    inst = internal_db.instances.find_one({"_id": ObjectId(instance["id"])})
    snap = ms.collect_snapshot(internal_db, inst, NoStatus())
    sv = snap["server"]
    assert sv["connections_current"] is None and sv["opcounters"] is None and sv["rates"] is None
    assert any("serverStatus" in u for u in snap["unavailable"])
    assert any("dbStats" in u for u in snap["unavailable"]) and snap["databases"] == []


def test_real_and_synthetic_patterns_with_same_fingerprint_coexist_without_leaking(
    client, admin, internal_db, instance, orders_db
):
    """Regression: SYNTHETIC demo patterns must never collide with, or leak into, real ones."""
    from app.analysis.query_fingerprint import fingerprint
    from app.services import slow_query_service as sqs

    iid = instance["id"]
    inst = internal_db.instances.find_one({"_id": ObjectId(iid)})
    fp = fingerprint(operation="find", collection="orders", filter_={"customer_id": 1, "status": "x"})
    internal_db.query_patterns.insert_one(
        {
            "instance_id": inst["_id"],
            "owner_id": inst["owner_id"],
            "fingerprint": fp,
            "database": orders_db,
            "collection": "orders",
            "operation": "find",
            "shape": {},
            "first_seen": datetime.now(UTC),
            "last_seen": datetime.now(UTC),
            "count": 999,
            "total_ms": 1.0,
            "max_ms": 1.0,
            "recent_durations_ms": [1.0],
            "sample_enc": None,
            "source": "SYNTHETIC",
        }
    )
    entry = {
        "op": "query",
        "ns": f"{orders_db}.orders",
        "ts": datetime.now(UTC),
        "millis": 50,
        "command": {"find": "orders", "filter": {"customer_id": 5, "status": "A"}},
        "planSummary": "COLLSCAN",
        "docsExamined": 60000,
        "nreturned": 3,
        "keysExamined": 0,
    }
    res = sqs.ingest_entries(internal_db, inst, [entry])  # would raise DuplicateKeyError before the fix
    assert res["ingested"] == 1
    sources = sorted(
        p["source"] for p in internal_db.query_patterns.find({"instance_id": inst["_id"], "fingerprint": fp})
    )
    assert sources == ["SYNTHETIC", "observed"]
    # the recommendation's "pattern observed N times" must count only REAL observations
    r = client.post(
        f"/api/v1/execution-plans/{iid}/explain",
        headers=admin["headers"],
        json={"database": orders_db, "collection": "orders", "filter": {"customer_id": 1, "status": "x"}},
    )
    evidence = " ".join(r.json()["plan"]["recommendation"].get("supporting_evidence", []))
    assert "999" not in evidence
