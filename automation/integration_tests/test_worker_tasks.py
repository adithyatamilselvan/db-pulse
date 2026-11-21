"""Integration: background worker tasks (the same functions Celery beat and the in-process
scheduler call): collection cycle, interval gating, failure handling and retention."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

pytestmark = pytest.mark.mongo


def test_collection_cycle_collects_metrics_and_slow_queries(instance, shop_app, orders_db, internal_db):
    from app.workers import tasks

    list(shop_app[orders_db].orders.find({"customer_id": 41, "status": "PENDING"}))
    iid = ObjectId(instance["id"])
    out = tasks.collect_instance(iid)
    assert out["metrics"] is True and out["slow"][orders_db]["ingested"] >= 1
    inst = internal_db.instances.find_one({"_id": iid})
    assert inst["status"] == "connected" and inst["last_metrics_at"] and inst["last_collection_at"]
    assert internal_db.metrics.count_documents({"instance_id": iid, "source": "observed"}) == 1
    assert internal_db.alerts.count_documents({"instance_id": iid, "type": "COLLECTION_SCAN"}) >= 1


def test_intervals_gate_repeat_work(instance, internal_db):
    from app.workers import tasks

    iid = ObjectId(instance["id"])
    tasks.collect_instance(iid)
    again = tasks.collect_instance(iid)  # both intervals (60 s) have not elapsed
    assert "metrics" not in again and "slow" not in again
    internal_db.instances.update_one(
        {"_id": iid}, {"$set": {"last_metrics_at": datetime.now(UTC) - timedelta(minutes=5)}}
    )
    assert tasks.collect_instance(iid).get("metrics") is True


def test_disabled_and_demo_instances_are_skipped(instance, internal_db):
    from app.workers import tasks

    iid = ObjectId(instance["id"])
    internal_db.instances.update_one({"_id": iid}, {"$set": {"enabled": False}})
    assert tasks.collect_instance(iid) == {"skipped": True}
    internal_db.instances.update_one({"_id": iid}, {"$set": {"enabled": True, "is_demo": True}})
    tasks.run_collection_cycle()
    assert internal_db.metrics.count_documents({"instance_id": iid}) == 0  # demo data is synthetic only
    internal_db.instances.update_one({"_id": iid}, {"$set": {"is_demo": False}})


def test_unreachable_instance_marks_error_and_raises_one_alert(instance, internal_db):
    from app.core.encryption import encrypt_secret
    from app.services import connection_service
    from app.workers import tasks

    iid = ObjectId(instance["id"])
    internal_db.instances.update_one(
        {"_id": iid}, {"$set": {"uri_enc": encrypt_secret("mongodb://127.0.0.1:1/?directConnection=true")}}
    )
    connection_service.pool.evict(str(iid))
    for _ in range(2):
        out = tasks.collect_instance(iid)
        assert "error" in out and "Cannot reach" in out["error"]
    inst = internal_db.instances.find_one({"_id": iid})
    assert inst["status"] == "error" and inst["last_error"]
    alerts = list(internal_db.alerts.find({"instance_id": iid, "type": "MONITORING_FAILURE"}))
    assert len(alerts) == 1 and alerts[0]["occurrences"] == 2  # deduplicated, counted


def test_retention_task_runs_across_instances(instance, internal_db):
    from app.workers import tasks

    iid = ObjectId(instance["id"])
    internal_db.metrics.insert_one(
        {
            "instance_id": iid,
            "owner_id": ObjectId(),
            "ts": datetime.now(UTC) - timedelta(days=999),
            "source": "observed",
            "server": {},
            "databases": [],
        }
    )
    assert tasks.run_retention_cleanup() >= 1
    assert (
        internal_db.metrics.count_documents(
            {"instance_id": iid, "ts": {"$lt": datetime.now(UTC) - timedelta(days=900)}}
        )
        == 0
    )


def test_inprocess_scheduler_starts_runs_and_stops(monkeypatch):
    from app.workers import scheduler as sched

    calls = {"n": 0}
    monkeypatch.setattr(
        sched.tasks, "run_collection_cycle", lambda: calls.__setitem__("n", calls["n"] + 1) or 0
    )

    class S:
        scheduler_tick_s = 1

    monkeypatch.setattr(sched, "get_settings", lambda: S())
    s = sched.Scheduler()
    s.start()
    time.sleep(2.4)
    s.stop()
    assert calls["n"] >= 2 and not s._thread.is_alive()


def test_celery_app_wires_the_same_tasks():
    from app.workers.celery_app import celery

    assert {"dbpulse.collect_cycle", "dbpulse.retention_cleanup"} <= set(celery.tasks)
    assert set(celery.conf.beat_schedule) == {"collect-cycle", "retention-cleanup"}
