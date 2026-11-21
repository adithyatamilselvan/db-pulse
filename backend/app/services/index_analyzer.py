"""Index investigation against a live monitored collection (I/O layer over analysis/)."""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId
from pymongo import MongoClient
from pymongo import errors as pe
from pymongo.database import Database

from app.analysis.index_analyzer import analyze_indexes
from app.core.exceptions import UnsupportedOperation, ValidationFailed
from app.services.connection_service import classify_error
from app.utils.serialization import utcnow

log = logging.getLogger("dbpulse.indexes")
CARDINALITY_SAMPLE = 1000


def list_indexes(client: MongoClient, db_name: str, coll: str) -> list[dict]:
    try:
        return [dict(i) for i in client[db_name][coll].list_indexes()]
    except pe.OperationFailure as exc:
        if exc.code == 26:  # NamespaceNotFound: collection does not exist
            return []
        raise UnsupportedOperation(classify_error(exc)[1]) from exc


def index_stats(client: MongoClient, db_name: str, coll: str) -> list[dict] | None:
    """``$indexStats``; ``None`` (not zero) when unavailable on this deployment/permissions."""
    try:
        return list(client[db_name][coll].aggregate([{"$indexStats": {}}]))
    except pe.PyMongoError:
        log.info("$indexStats unavailable for %s.%s", db_name, coll)
        return None


def index_sizes(client: MongoClient, db_name: str, coll: str) -> dict[str, int]:
    try:
        return dict(client[db_name].command("collStats", coll).get("indexSizes", {}))
    except pe.PyMongoError:
        return {}


def investigate_indexes(client: MongoClient, db_name: str, coll: str) -> dict[str, Any]:
    idx = list_indexes(client, db_name, coll)
    stats = index_stats(client, db_name, coll)
    result = analyze_indexes(idx, stats)
    sizes = index_sizes(client, db_name, coll)
    for row in result["indexes"]:
        row["size_bytes"] = sizes.get(row["name"])
    result.update(database=db_name, collection=coll)
    return result


def estimate_cardinality(client: MongoClient, db_name: str, coll: str, fields: list[str]) -> dict[str, int]:
    """Distinct-value count per field over a small random sample (an *estimate*)."""
    if not fields:
        return {}
    try:
        group = {"_id": None, **{f"f{i}": {"$addToSet": f"${f}"} for i, f in enumerate(fields)}}
        rows = list(
            client[db_name][coll].aggregate(
                [{"$sample": {"size": CARDINALITY_SAMPLE}}, {"$group": group}], maxTimeMS=5000
            )
        )
    except pe.PyMongoError:
        return {}
    if not rows:
        return {}
    return {f: len(rows[0].get(f"f{i}", [])) for i, f in enumerate(fields)}


def create_index_confirmed(
    client: MongoClient,
    internal: Database,
    *,
    instance: dict,
    user_id: Any,
    db_name: str,
    coll: str,
    keys: list[tuple[str, int]],
    name: str | None,
    confirmed: bool,
    acknowledged_write_cost: bool,
) -> dict[str, Any]:
    """Create an index on the monitored DB. Requires explicit confirmation + instance opt-in."""
    if not instance.get("allow_modifications"):
        raise ValidationFailed(
            "This instance is registered read-only. Enable 'allow modifications' in its "
            "settings before DB Pulse may create indexes on it."
        )
    if not (confirmed and acknowledged_write_cost):
        raise ValidationFailed(
            "Creating an index modifies the monitored database and adds write/storage cost. "
            "Set confirm=true and acknowledge_write_cost=true to proceed."
        )
    if not keys or len(keys) > 8:
        raise ValidationFailed("An index needs between 1 and 8 key fields")
    for f, d in keys:
        if not isinstance(f, str) or not f or f.startswith("$") or d not in (1, -1):
            raise ValidationFailed("Index keys must be [field, 1|-1] pairs with plain field names")
    try:
        created = client[db_name][coll].create_index(keys, **({"name": name} if name else {}))
    except pe.PyMongoError as exc:
        raise UnsupportedOperation(f"Index creation failed: {classify_error(exc)[1]}") from exc
    change = {
        "instance_id": instance["_id"],
        "owner_id": instance["owner_id"],
        "user_id": user_id,
        "action": "create",
        "database": db_name,
        "collection": coll,
        "index_name": created,
        "keys": [[f, d] for f, d in keys],
        "created_at": utcnow(),
    }
    change["_id"] = internal.index_changes.insert_one(change).inserted_id
    return change


def drop_index_created_by_dbpulse(
    client: MongoClient,
    internal: Database,
    *,
    instance: dict,
    user_id: Any,
    change_id: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Roll back an index that DB Pulse itself created. Arbitrary drops are not supported."""
    if not confirmed:
        raise ValidationFailed("Dropping an index requires confirm=true")
    try:
        oid = ObjectId(change_id)
    except Exception as exc:
        raise ValidationFailed("Invalid change id") from exc
    change = internal.index_changes.find_one({"_id": oid, "instance_id": instance["_id"], "action": "create"})
    if not change:
        raise ValidationFailed("Only indexes created through DB Pulse can be dropped here")
    if internal.index_changes.find_one({"rollback_of": oid}):
        raise ValidationFailed("This index change was already rolled back")
    try:
        client[change["database"]][change["collection"]].drop_index(change["index_name"])
    except pe.PyMongoError as exc:
        raise UnsupportedOperation(f"Index drop failed: {classify_error(exc)[1]}") from exc
    rollback = {
        "instance_id": instance["_id"],
        "owner_id": instance["owner_id"],
        "user_id": user_id,
        "action": "drop",
        "rollback_of": oid,
        "database": change["database"],
        "collection": change["collection"],
        "index_name": change["index_name"],
        "created_at": utcnow(),
    }
    rollback["_id"] = internal.index_changes.insert_one(rollback).inserted_id
    return rollback
