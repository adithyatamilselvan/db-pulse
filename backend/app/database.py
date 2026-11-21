"""DB Pulse's *internal* MongoDB (metadata store). Never used for monitored data."""

from __future__ import annotations

import contextlib
import logging
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure

from app.config import get_settings

log = logging.getLogger("dbpulse.db")


@lru_cache
def get_client() -> MongoClient:
    s = get_settings()
    return MongoClient(
        s.internal_mongo_uri,
        serverSelectionTimeoutMS=s.mongo_server_selection_timeout_ms,
        tz_aware=True,
        appname="dbpulse-internal",
    )


def get_internal_db() -> Database:
    return get_client()[get_settings().internal_db_name]


def reset_client_cache() -> None:
    try:
        get_client().close()
    except Exception:
        log.debug("client close failed", exc_info=True)
    get_client.cache_clear()


def ensure_indexes(db: Database | None = None) -> None:
    db = db if db is not None else get_internal_db()
    db.users.create_index("email", unique=True)
    db.instances.create_index([("owner_id", ASCENDING), ("name", ASCENDING)], unique=True)
    db.metrics.create_index([("instance_id", ASCENDING), ("ts", DESCENDING)])
    db.slow_queries.create_index([("instance_id", ASCENDING), ("ts", DESCENDING)])
    db.slow_queries.create_index([("instance_id", ASCENDING), ("dedupe_key", ASCENDING)], unique=True)
    db.slow_queries.create_index([("instance_id", ASCENDING), ("fingerprint", ASCENDING)])
    # Real and SYNTHETIC patterns may share a fingerprint, so `source` is part of the key.
    with contextlib.suppress(OperationFailure):
        db.query_patterns.drop_index("instance_id_1_fingerprint_1")  # pre-1.0 index shape
    db.query_patterns.create_index(
        [("instance_id", ASCENDING), ("fingerprint", ASCENDING), ("source", ASCENDING)], unique=True
    )
    db.execution_plans.create_index([("instance_id", ASCENDING), ("created_at", DESCENDING)])
    db.index_analyses.create_index([("instance_id", ASCENDING), ("created_at", DESCENDING)])
    db.alerts.create_index([("instance_id", ASCENDING), ("created_at", DESCENDING)])
    db.alerts.create_index([("instance_id", ASCENDING), ("dedupe_key", ASCENDING), ("status", ASCENDING)])
    db.benchmarks.create_index([("instance_id", ASCENDING), ("created_at", DESCENDING)])
    db.comparisons.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
    db.reports.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
    db.investigations.create_index([("owner_id", ASCENDING), ("updated_at", DESCENDING)])
    db.index_changes.create_index([("instance_id", ASCENDING), ("created_at", DESCENDING)])
    db.profiler_changes.create_index([("instance_id", ASCENDING), ("created_at", DESCENDING)])
