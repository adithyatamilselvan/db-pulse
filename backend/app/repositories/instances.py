"""Persistence helpers for registered monitored instances. Credentials never leave this layer."""

from __future__ import annotations

import re
from typing import Any

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.encryption import encrypt_secret
from app.core.exceptions import ConflictError
from app.models.enums import DEFAULT_INSTANCE_SETTINGS
from app.utils.serialization import to_jsonable, utcnow

_HOSTS = re.compile(r"^mongodb(?:\+srv)?://(?:[^@/]*@)?([^/?]+)", re.IGNORECASE)
_PUBLIC_FIELDS = (
    "name",
    "host_display",
    "status",
    "last_check_at",
    "last_error",
    "server_version",
    "allow_modifications",
    "enabled",
    "is_demo",
    "created_at",
    "settings",
    "last_collection_at",
    "last_metrics_at",
    "last_collection_result",
)


def host_display(uri: str) -> str:
    m = _HOSTS.match(uri)
    return m.group(1) if m else "unknown"


def create_instance(
    db: Database,
    *,
    owner_id: ObjectId,
    name: str,
    uri: str,
    allow_modifications: bool,
    server_version: str | None,
    is_demo: bool = False,
) -> dict[str, Any]:
    doc = {
        "owner_id": owner_id,
        "name": name,
        "uri_enc": encrypt_secret(uri),
        "host_display": host_display(uri),
        "allow_modifications": allow_modifications,
        "enabled": True,
        "status": "connected",
        "last_check_at": utcnow(),
        "last_error": None,
        "server_version": server_version,
        "settings": dict(DEFAULT_INSTANCE_SETTINGS),
        "checkpoints": {},
        "is_demo": is_demo,
        "created_at": utcnow(),
    }
    try:
        doc["_id"] = db.instances.insert_one(doc).inserted_id
    except DuplicateKeyError as exc:
        raise ConflictError(f"You already registered an instance named '{name}'") from exc
    return doc


def public(inst: dict[str, Any]) -> dict[str, Any]:
    """API projection: whitelist of fields, so the encrypted URI can never be serialized."""
    out = {k: inst.get(k) for k in _PUBLIC_FIELDS}
    out["_id"] = inst["_id"]
    return to_jsonable(out)


def delete_instance_data(db: Database, instance_id: ObjectId) -> dict[str, int]:
    counts = {}
    for coll in (
        "metrics",
        "slow_queries",
        "query_patterns",
        "execution_plans",
        "index_analyses",
        "alerts",
        "benchmarks",
        "comparisons",
        "index_changes",
        "profiler_changes",
        "investigations",
    ):
        counts[coll] = db[coll].delete_many({"instance_id": instance_id}).deleted_count
    db.instances.delete_one({"_id": instance_id})
    return counts
