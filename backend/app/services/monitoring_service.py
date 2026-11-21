"""Server/database metric collection, history retrieval and retention.

Metrics a deployment does not expose are stored as ``None`` and listed in ``unavailable`` --
they are never reported as zero.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from pymongo import MongoClient
from pymongo import errors as pe
from pymongo.database import Database

from app.services.connection_service import classify_error
from app.services.slow_query_service import instance_settings, target_databases
from app.utils.serialization import as_aware, utcnow

log = logging.getLogger("dbpulse.monitoring")
_OPS = ("insert", "query", "update", "delete", "getmore", "command")


def _rates(prev: dict | None, cur: dict | None, dt: float) -> dict | None:
    if not prev or not cur or dt <= 0:
        return None
    out = {}
    for k in _OPS:
        a, b = prev.get(k), cur.get(k)
        if a is None or b is None or b < a:  # counter reset (restart) => unknown, not negative
            out[k] = None
        else:
            out[k] = round((b - a) / dt, 3)
    return out


def _op_latency(prev: dict | None, cur: dict | None) -> dict[str, float | None]:
    out: dict[str, float | None] = {"reads": None, "writes": None, "commands": None}
    if not cur:
        return out
    for k in out:
        c = cur.get(k) or {}
        p = (prev or {}).get(k) or {}
        ops = c.get("ops", 0) - p.get("ops", 0)
        lat = c.get("latency", 0) - p.get("latency", 0)
        if ops > 0 and lat >= 0:
            out[k] = round(lat / ops / 1000.0, 3)  # microseconds -> ms
    return out


def collect_snapshot(db: Database, instance: dict, client: MongoClient) -> dict[str, Any]:
    unavailable: list[str] = []
    now = utcnow()
    prev = db.metrics.find_one({"instance_id": instance["_id"], "source": "observed"}, sort=[("ts", -1)])
    status: dict[str, Any] = {}
    try:
        status = client.admin.command("serverStatus")
    except pe.OperationFailure as exc:
        # permission / unsupported => this metric is unavailable. Connectivity errors are NOT
        # swallowed here: an unreachable server is a monitoring failure, not "no metrics".
        unavailable.append(f"serverStatus: {classify_error(exc)[1]}")
    server: dict[str, Any] = {
        "version": status.get("version"),
        "uptime_s": status.get("uptime"),
        "connections_current": (status.get("connections") or {}).get("current"),
        "connections_available": (status.get("connections") or {}).get("available"),
        "mem_resident_mb": (status.get("mem") or {}).get("resident"),
        "mem_virtual_mb": (status.get("mem") or {}).get("virtual"),
        "cache_bytes": ((status.get("wiredTiger") or {}).get("cache") or {}).get(
            "bytes currently in the cache"
        ),
    }
    opcounters = status.get("opcounters")
    if status and not opcounters:
        unavailable.append("opcounters not exposed")
    prev_server = (prev or {}).get("server", {})
    dt = (now - as_aware(prev["ts"])).total_seconds() if prev else 0
    restarted = bool(
        prev
        and server["uptime_s"] is not None
        and prev_server.get("uptime_s") is not None
        and server["uptime_s"] < prev_server["uptime_s"]
    )
    server["opcounters"] = opcounters
    server["rates"] = None if restarted else _rates(prev_server.get("opcounters"), opcounters, dt)
    server["op_latency_ms"] = _op_latency(
        None if restarted else prev_server.get("_op_latencies_raw"), status.get("opLatencies")
    )
    server["_op_latencies_raw"] = status.get("opLatencies")
    if status and not status.get("opLatencies"):
        unavailable.append("opLatencies not exposed")

    databases = []
    for name in target_databases(client, instance):
        try:
            st = client[name].command("dbStats")
            databases.append(
                {
                    "name": name,
                    "data_size": st.get("dataSize"),
                    "storage_size": st.get("storageSize"),
                    "index_size": st.get("indexSize"),
                    "collections": st.get("collections"),
                    "indexes": st.get("indexes"),
                    "objects": st.get("objects"),
                }
            )
        except pe.OperationFailure as exc:
            unavailable.append(f"dbStats[{name}]: {classify_error(exc)[1]}")
    doc = {
        "instance_id": instance["_id"],
        "owner_id": instance["owner_id"],
        "ts": now,
        "source": "observed",
        "server": server,
        "databases": databases,
        "unavailable": unavailable,
    }
    db.metrics.insert_one(doc)
    db.instances.update_one(
        {"_id": instance["_id"]}, {"$set": {"last_metrics_at": now, "server_version": server["version"]}}
    )
    return doc


def history(
    db: Database,
    instance_id: Any,
    *,
    start: datetime,
    end: datetime,
    source: str | None = None,
    max_points: int = 500,
) -> list[dict]:
    q: dict[str, Any] = {"instance_id": instance_id, "ts": {"$gte": start, "$lte": end}}
    if source:
        q["source"] = source
    rows = list(db.metrics.find(q, {"server._op_latencies_raw": 0}).sort("ts", 1))
    if len(rows) > max_points:  # evenly thin for charting; raw rows stay stored
        step = len(rows) / max_points
        rows = [rows[int(i * step)] for i in range(max_points)]
    return rows


PERIODS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def resolve_period(
    period: str | None, start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    from app.core.exceptions import ValidationFailed

    now = utcnow()
    if period == "custom" or (start and end):
        if not (start and end) or start >= end:
            raise ValidationFailed("Custom range requires start < end")
        return as_aware(start), as_aware(end)
    delta = PERIODS.get(period or "24h")
    if delta is None:
        raise ValidationFailed(f"period must be one of {sorted(PERIODS)} or 'custom'")
    return now - delta, now


def apply_retention(db: Database, instance: dict) -> dict[str, int]:
    days = int(instance_settings(instance)["retention_days"])
    cutoff = utcnow() - timedelta(days=days)
    iid = instance["_id"]
    return {
        "metrics": db.metrics.delete_many({"instance_id": iid, "ts": {"$lt": cutoff}}).deleted_count,
        "slow_queries": db.slow_queries.delete_many(
            {"instance_id": iid, "ts": {"$lt": cutoff}}
        ).deleted_count,
        "alerts": db.alerts.delete_many(
            {"instance_id": iid, "status": "resolved", "resolved_at": {"$lt": cutoff}}
        ).deleted_count,
        "execution_plans": db.execution_plans.delete_many(
            {"instance_id": iid, "created_at": {"$lt": cutoff}}
        ).deleted_count,
    }
