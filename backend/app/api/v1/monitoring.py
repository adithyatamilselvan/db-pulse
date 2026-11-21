from __future__ import annotations

from datetime import datetime
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Query

from app.core.dependencies import DB, AdminUser, CurrentUser, accessible_instance_ids, load_instance
from app.core.exceptions import ConnectionFailed, NotFoundError, ValidationFailed
from app.repositories import instances as repo
from app.schemas.common import ProfilerRestoreIn, ProfilerSetIn
from app.services import connection_service as conn
from app.services import monitoring_service, profiler_service, slow_query_service
from app.utils.serialization import to_jsonable, utcnow
from app.utils.stats import percentile

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


def _client(instance: dict):  # type: ignore[no-untyped-def]
    if not instance.get("enabled", True):
        raise ValidationFailed("This instance is disconnected; reconnect it first")
    return conn.get_monitored_client(instance)


def _mark_failure(db: DB, inst: dict, exc: Exception) -> None:
    from app.services import alert_service

    _, msg = conn.classify_error(exc)
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


@router.post("/{instance_id}/collect")
def collect_metrics(instance_id: str, user: CurrentUser, db: DB) -> dict:
    """Take a real metrics snapshot now."""
    inst = load_instance(db, instance_id, user)
    try:
        snap = monitoring_service.collect_snapshot(db, inst, _client(inst))
    except ValidationFailed:
        raise
    except Exception as exc:
        _mark_failure(db, inst, exc)
        raise ConnectionFailed(f"Metric collection failed: {conn.classify_error(exc)[1]}") from exc
    db.instances.update_one({"_id": inst["_id"]}, {"$set": {"status": "connected"}})
    return {"snapshot": to_jsonable(snap)}


@router.post("/{instance_id}/collect-slow")
def collect_slow(instance_id: str, user: CurrentUser, db: DB) -> dict:
    """Read new profiler entries now and run slow-query detection, alerts and fingerprinting."""
    inst = load_instance(db, instance_id, user)
    try:
        results = slow_query_service.collect_for_instance(db, inst, _client(inst))
    except ValidationFailed:
        raise
    except Exception as exc:
        _mark_failure(db, inst, exc)
        raise ConnectionFailed(f"Slow query collection failed: {conn.classify_error(exc)[1]}") from exc
    return {"results": results}


@router.get("/{instance_id}/metrics")
def metrics(
    instance_id: str,
    user: CurrentUser,
    db: DB,
    period: str = "1h",
    start: datetime | None = None,
    end: datetime | None = None,
    include_synthetic: bool = True,
) -> dict:
    inst = load_instance(db, instance_id, user)
    s, e = monitoring_service.resolve_period(period, start, end)
    rows = monitoring_service.history(
        db, inst["_id"], start=s, end=e, source=None if include_synthetic else "observed"
    )
    return {"period": {"start": s.isoformat(), "end": e.isoformat()}, "points": to_jsonable(rows)}


@router.get("/{instance_id}/profiler")
def profiler_status(instance_id: str, database: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    status = profiler_service.get_profiler_status(_client(inst), database)
    changes = (
        db.profiler_changes.find({"instance_id": inst["_id"], "database": database})
        .sort("created_at", -1)
        .limit(10)
    )
    return {
        "status": status,
        "changes": to_jsonable(list(changes)),
        "warning": "Enabling profiling adds overhead on the monitored database.",
    }


@router.post("/{instance_id}/profiler")
def set_profiler(instance_id: str, body: ProfilerSetIn, user: AdminUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    if not inst.get("allow_modifications"):
        raise ValidationFailed("This instance is registered read-only. Enable 'allow modifications' first.")
    res = profiler_service.set_profiling(
        _client(inst),
        body.database,
        level=body.level,
        slowms=body.slowms,
        sample_rate=body.sample_rate,
        confirmed=body.confirm,
    )
    change = profiler_service.record_change(
        db,
        instance=inst,
        user_id=user["_id"],
        database=body.database,
        action="set",
        previous=res["previous"],
        current=res["current"],
    )
    return {"change_id": str(change["_id"]), **to_jsonable(res)}


@router.post("/{instance_id}/profiler/restore")
def restore_profiler(instance_id: str, body: ProfilerRestoreIn, user: AdminUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    if not body.confirm:
        raise ValidationFailed("Restoring the profiler configuration requires confirm=true")
    from bson import ObjectId

    try:
        change = db.profiler_changes.find_one(
            {"_id": ObjectId(body.change_id), "instance_id": inst["_id"], "action": "set"}
        )
    except Exception as exc:
        raise NotFoundError("Profiler change not found") from exc
    if not change:
        raise NotFoundError("Profiler change not found")
    current = profiler_service.restore_profiling(_client(inst), change["database"], change["previous"])
    profiler_service.record_change(
        db,
        instance=inst,
        user_id=user["_id"],
        database=change["database"],
        action="restore",
        previous=change["current"],
        current=current,
    )
    return {"restored": to_jsonable(current)}


@router.post("/{instance_id}/cleanup")
def cleanup(instance_id: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    return {"removed": monitoring_service.apply_retention(db, inst)}


@router.get("/dashboard")
def dashboard(
    user: CurrentUser,
    db: DB,
    period: Annotated[str, Query()] = "24h",
    start: datetime | None = None,
    end: datetime | None = None,
    include_synthetic: bool = True,
) -> dict:
    """Aggregated dashboard numbers across every instance the user can access."""
    s, e = monitoring_service.resolve_period(period, start, end)
    ids = accessible_instance_ids(db, user)
    src = {} if include_synthetic else {"source": "observed"}
    insts = list(db.instances.find({"_id": {"$in": ids}}))
    q = {"instance_id": {"$in": ids}, "ts": {"$gte": s, "$lte": e}, **src}
    rows = list(
        db.slow_queries.find(
            q, {"ts": 1, "duration_ms": 1, "collection_scan": 1, "index_scan": 1, "source": 1}
        ).limit(100_000)
    )
    durations = [r["duration_ms"] for r in rows]
    trend: list[dict] = []
    if rows:
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        span = (e - s).total_seconds()
        freq = "1min" if span <= 3600 else "15min" if span <= 86400 else "1h" if span <= 7 * 86400 else "6h"
        g = df.set_index("ts")["duration_ms"].resample(freq)
        agg = pd.DataFrame(
            {"count": g.count(), "avg": g.mean(), "p95": g.quantile(0.95), "p99": g.quantile(0.99)}
        ).dropna(subset=["avg"])
        trend = [
            {
                "ts": ts.isoformat(),
                "count": int(r["count"]),
                "avg_ms": float(r["avg"]),
                "p95_ms": float(r["p95"]),
                "p99_ms": float(r["p99"]),
            }
            for ts, r in agg.iterrows()
        ]
    scans = sum(1 for r in rows if r.get("collection_scan"))
    idx = sum(1 for r in rows if r.get("index_scan"))
    open_alerts = db.alerts.count_documents({"instance_id": {"$in": ids}, "status": "open", **src})
    by_sev = {
        r["_id"]: r["n"]
        for r in db.alerts.aggregate(
            [
                {"$match": {"instance_id": {"$in": ids}, "status": {"$in": ["open", "acknowledged"]}, **src}},
                {"$group": {"_id": "$severity", "n": {"$sum": 1}}},
            ]
        )
    }
    inv = list(
        db.investigations.find({"instance_id": {"$in": ids}}, {"title": 1, "updated_at": 1, "instance_id": 1})
        .sort("updated_at", -1)
        .limit(5)
    )
    return to_jsonable(
        {
            "period": {"start": s, "end": e},
            "instances": {
                "total": len(insts),
                "connected": sum(1 for i in insts if i.get("status") == "connected"),
                "items": [repo.public(i) for i in insts],
            },
            "slow_queries": {
                "total": len(rows),
                "avg_ms": (sum(durations) / len(durations)) if durations else None,
                "p95_ms": percentile(durations, 95),
                "p99_ms": percentile(durations, 99),
                "note": "Computed over recorded slow operations only (those above the "
                "configured threshold), not over all queries.",
            },
            "collection_scans": scans,
            "index_usage": {
                "index_scans": idx,
                "collection_scans": scans,
                "index_scan_share": (idx / (idx + scans)) if (idx + scans) else None,
            },
            "active_alerts": {"open": open_alerts, "by_severity": by_sev},
            "recent_investigations": inv,
            "trend": trend,
            "includes_synthetic": any(r.get("source") == "SYNTHETIC" for r in rows),
        }
    )
