"""Background jobs. Plain functions: callable from the in-process scheduler or from Celery."""

from __future__ import annotations

import logging
from datetime import timedelta

from app.database import get_internal_db
from app.services import alert_service, monitoring_service, slow_query_service
from app.services import connection_service as conn
from app.utils.serialization import as_aware, utcnow

log = logging.getLogger("dbpulse.worker")


def _due(last, interval_s: int) -> bool:
    return last is None or utcnow() - as_aware(last) >= timedelta(seconds=interval_s)


def collect_instance(instance_id) -> dict:  # type: ignore[no-untyped-def]
    """One monitoring + detection cycle for an instance, honouring its configured intervals."""
    db = get_internal_db()
    inst = db.instances.find_one({"_id": instance_id})
    if not inst or not inst.get("enabled", True):
        return {"skipped": True}
    settings = slow_query_service.instance_settings(inst)
    out: dict = {}
    try:
        client = conn.get_monitored_client(inst)
        if _due(inst.get("last_metrics_at"), settings["monitoring_interval_s"]):
            monitoring_service.collect_snapshot(db, inst, client)
            out["metrics"] = True
        if _due(inst.get("last_collection_at"), settings["detection_interval_s"]):
            out["slow"] = slow_query_service.collect_for_instance(db, inst, client)
        db.instances.update_one({"_id": inst["_id"]}, {"$set": {"status": "connected", "last_error": None}})
    except Exception as exc:
        _, msg = conn.classify_error(exc)
        log.warning("collection failed for %s: %s", inst["name"], msg)
        db.instances.update_one(
            {"_id": inst["_id"]}, {"$set": {"status": "error", "last_error": msg, "last_check_at": utcnow()}}
        )
        alert_service.raise_alert(
            db,
            instance=inst,
            alert_type="MONITORING_FAILURE",
            severity="HIGH",
            title=f"Monitoring failed for {inst['name']}",
            message=msg,
        )
        out["error"] = msg
    return out


def run_collection_cycle() -> int:
    db = get_internal_db()
    n = 0
    for inst in db.instances.find({"enabled": True, "is_demo": {"$ne": True}}, {"_id": 1}):
        collect_instance(inst["_id"])
        n += 1
    return n


def run_retention_cleanup() -> int:
    db = get_internal_db()
    n = 0
    for inst in db.instances.find({}):
        monitoring_service.apply_retention(db, inst)
        n += 1
    return n
