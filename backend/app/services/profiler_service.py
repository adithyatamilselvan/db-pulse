"""MongoDB profiler integration.

Mode A (read an existing profiler) is read-only. Mode B (enabling profiling) changes the
monitored database, so it only runs through :func:`set_profiling` which requires explicit
confirmation and records the previous configuration so it can be restored.
Profiler availability and permissions vary; every call degrades to an explicit
``available: False`` with a reason instead of raising.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pymongo import MongoClient
from pymongo import errors as pe
from pymongo.database import Database

from app.core.exceptions import UnsupportedOperation, ValidationFailed
from app.services.connection_service import classify_error
from app.utils.serialization import utcnow

log = logging.getLogger("dbpulse.profiler")
LEVELS = {0: "off", 1: "slow operations only", 2: "all operations"}


def get_profiler_status(client: MongoClient, db_name: str) -> dict[str, Any]:
    try:
        res = client[db_name].command("profile", -1)
    except pe.PyMongoError as exc:
        code, msg = classify_error(exc)
        return {"database": db_name, "available": False, "reason": msg, "reason_code": code}
    level = int(res.get("was", 0))
    status = {
        "database": db_name,
        "available": True,
        "level": level,
        "level_description": LEVELS.get(level, "unknown"),
        "slowms": res.get("slowms"),
        "sample_rate": res.get("sampleRate"),
        "system_profile_present": "system.profile" in _collection_names(client[db_name]),
    }
    return status


def _collection_names(db: Database) -> list[str]:
    try:
        return db.list_collection_names(filter={"name": "system.profile"})
    except pe.PyMongoError:
        return []


def set_profiling(
    client: MongoClient, db_name: str, *, level: int, slowms: int, sample_rate: float = 1.0, confirmed: bool
) -> dict[str, Any]:
    """Change the profiler on a monitored database. Returns previous + new configuration."""
    if not confirmed:
        raise ValidationFailed(
            "Changing profiler settings modifies the monitored database and adds overhead. "
            "Explicit confirmation is required."
        )
    if level not in (0, 1, 2):
        raise ValidationFailed("Profiling level must be 0, 1 or 2")
    if not 0 <= slowms <= 3_600_000:
        raise ValidationFailed("slowms must be between 0 and 3600000")
    if not 0 < sample_rate <= 1:
        raise ValidationFailed("sample_rate must be in (0, 1]")
    previous = get_profiler_status(client, db_name)
    if not previous["available"]:
        raise UnsupportedOperation(f"Profiler is not available here: {previous['reason']}")
    try:
        client[db_name].command("profile", level, slowms=slowms, sampleRate=sample_rate)
    except pe.PyMongoError as exc:
        raise UnsupportedOperation(classify_error(exc)[1]) from exc
    return {"previous": previous, "current": get_profiler_status(client, db_name)}


def restore_profiling(client: MongoClient, db_name: str, previous: dict[str, Any]) -> dict:
    """Restore a previously recorded configuration (used by the 'restore' control)."""
    level = int(previous.get("level", 0))
    slowms = int(previous.get("slowms") or 100)
    sample = float(previous.get("sample_rate") or 1.0)
    try:
        client[db_name].command("profile", level, slowms=slowms, sampleRate=sample)
    except pe.PyMongoError as exc:
        raise UnsupportedOperation(classify_error(exc)[1]) from exc
    return get_profiler_status(client, db_name)


def read_profile_entries(
    client: MongoClient, db_name: str, *, since: datetime | None, limit: int = 500
) -> dict[str, Any]:
    """Read system.profile entries newer than ``since``.

    Operations issued by DB Pulse itself (appName starting 'dbpulse') are excluded so the tool
    does not analyse its own probes and benchmarks.
    """
    status = get_profiler_status(client, db_name)
    if not status["available"]:
        return {"available": False, "reason": status["reason"], "entries": [], "status": status}
    if status["level"] == 0 and not status["system_profile_present"]:
        return {
            "available": False,
            "reason": "Profiling is off and system.profile does not exist for this database",
            "entries": [],
            "status": status,
        }
    query: dict[str, Any] = {
        "ns": {"$not": {"$regex": r"\.system\.profile$"}},
        "appName": {"$not": {"$regex": "^dbpulse"}},
        "op": {"$ne": "none"},
    }
    if since is not None:
        query["ts"] = {"$gt": since}
    try:
        entries = list(client[db_name]["system.profile"].find(query).sort("ts", 1).limit(limit))
    except pe.PyMongoError as exc:
        code, msg = classify_error(exc)
        return {"available": False, "reason": msg, "reason_code": code, "entries": [], "status": status}
    return {"available": True, "entries": entries, "status": status, "reason": None}


def record_change(
    db: Database, *, instance: dict, user_id: Any, database: str, action: str, previous: dict, current: dict
) -> dict:
    doc = {
        "instance_id": instance["_id"],
        "owner_id": instance["owner_id"],
        "user_id": user_id,
        "database": database,
        "action": action,
        "previous": previous,
        "current": current,
        "created_at": utcnow(),
    }
    doc["_id"] = db.profiler_changes.insert_one(doc).inserted_id
    return doc
