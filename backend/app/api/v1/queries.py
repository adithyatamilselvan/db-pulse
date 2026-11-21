from __future__ import annotations

import re
from datetime import datetime

from bson import json_util
from fastapi import APIRouter, Query

from app.core.dependencies import DB, CurrentUser, accessible_instance_ids, load_instance
from app.core.encryption import decrypt_secret
from app.core.exceptions import NotFoundError, UnsupportedOperation
from app.services import connection_service as conn
from app.services import investigation_service, monitoring_service
from app.services.slow_query_service import pattern_stats
from app.utils.query_validation import validate_query_spec
from app.utils.serialization import to_jsonable

router = APIRouter(prefix="/queries", tags=["queries"])
SORTABLE = {"ts", "duration_ms", "docs_examined", "docs_returned", "efficiency_ratio"}


@router.get("/slow")
def slow_queries(
    user: CurrentUser,
    db: DB,
    instance_id: str | None = None,
    period: str = "24h",
    start: datetime | None = None,
    end: datetime | None = None,
    collection: str | None = None,
    search: str | None = None,
    severity: str | None = None,
    collection_scan: bool | None = None,
    sort: str = "ts",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_synthetic: bool = True,
) -> dict:
    s, e = monitoring_service.resolve_period(period, start, end)
    if instance_id:
        ids = [load_instance(db, instance_id, user)["_id"]]
    else:
        ids = accessible_instance_ids(db, user)
    q: dict = {"instance_id": {"$in": ids}, "ts": {"$gte": s, "$lte": e}}
    if not include_synthetic:
        q["source"] = "observed"
    if collection:
        q["collection"] = collection
    if severity:
        q["severity"] = severity.upper()
    if collection_scan is not None:
        q["collection_scan"] = collection_scan
    if search:
        rx = {"$regex": re.escape(search[:80]), "$options": "i"}
        q["$or"] = [
            {"fingerprint": rx},
            {"collection": rx},
            {"database": rx},
            {"plan_summary": rx},
            {"operation": rx},
        ]
    key = sort if sort in SORTABLE else "ts"
    total = db.slow_queries.count_documents(q)
    rows = list(
        db.slow_queries.find(q, {"dedupe_key": 0, "query_hash": 0})
        .sort(key, -1 if order == "desc" else 1)
        .skip(offset)
        .limit(limit)
    )
    return {"total": total, "items": to_jsonable(rows), "limit": limit, "offset": offset}


@router.get("/patterns")
def patterns(
    user: CurrentUser,
    db: DB,
    instance_id: str | None = None,
    period: str = "24h",
    start: datetime | None = None,
    end: datetime | None = None,
    include_synthetic: bool = True,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    s, _ = monitoring_service.resolve_period(period, start, end)
    ids = [load_instance(db, instance_id, user)["_id"]] if instance_id else accessible_instance_ids(db, user)
    q: dict = {"instance_id": {"$in": ids}, "last_seen": {"$gte": s}}
    if not include_synthetic:
        q["source"] = "observed"
    rows = list(db.query_patterns.find(q, {"sample_enc": 0}).sort("total_ms", -1).limit(limit))
    items = [{**to_jsonable(p), "stats": pattern_stats(p)} for p in rows]
    for it in items:
        it.pop("recent_durations_ms", None)
    return {"items": items}


def _find_pattern(
    db: DB, inst: dict, fingerprint: str, source: str | None, projection: dict | None = None
) -> dict:
    """Real and SYNTHETIC patterns can share a fingerprint. Prefer the real one unless the caller
    explicitly asks for a source."""
    q: dict = {"instance_id": inst["_id"], "fingerprint": fingerprint}
    if source:
        q["source"] = source
    pat = db.query_patterns.find_one(q, projection, sort=[("source", -1)])  # "observed" sorts first
    if not pat:
        raise NotFoundError("Query pattern not found")
    return pat


@router.get("/patterns/{instance_id}/{fingerprint}")
def pattern_detail(
    instance_id: str, fingerprint: str, user: CurrentUser, db: DB, source: str | None = None
) -> dict:
    inst = load_instance(db, instance_id, user)
    pat = _find_pattern(db, inst, fingerprint, source, {"sample_enc": 0})
    same = {"instance_id": inst["_id"], "fingerprint": fingerprint, "source": pat["source"]}
    executions = list(
        db.slow_queries.find(same, {"dedupe_key": 0, "query_hash": 0}).sort("ts", -1).limit(100)
    )
    plans = []
    if pat["source"] == "observed":  # explain plans only ever exist for real queries
        plans = list(
            db.execution_plans.find(
                {"instance_id": inst["_id"], "fingerprint": fingerprint},
                {"raw_explain_enc": 0, "index_analysis": 0},
            )
            .sort("created_at", -1)
            .limit(10)
        )
    alerts = list(db.alerts.find(same).sort("created_at", -1).limit(20))
    return to_jsonable(
        {
            "pattern": {**pat, "stats": pattern_stats(pat)},
            "executions": executions,
            "plans": plans,
            "alerts": alerts,
        }
    )


@router.post("/patterns/{instance_id}/{fingerprint}/investigate")
def investigate_pattern(
    instance_id: str, fingerprint: str, user: CurrentUser, db: DB, source: str | None = None
) -> dict:
    """Re-run a real explain(executionStats) using the pattern's most recent (encrypted) sample."""
    inst = load_instance(db, instance_id, user)
    pat = _find_pattern(db, inst, fingerprint, source)
    if pat.get("source") == "SYNTHETIC" or not pat.get("sample_enc"):
        raise UnsupportedOperation(
            "This pattern is a SYNTHETIC demo record with no real query to explain. "
            "Use the Execution plan explorer to explain a real query."
        )
    cmd = json_util.loads(decrypt_secret(pat["sample_enc"]))
    op = pat["operation"]
    if op not in ("find", "count", "aggregate"):
        raise UnsupportedOperation(f"Explain is not supported for '{op}' operations")
    spec = validate_query_spec(
        {
            "operation": op,
            "database": pat["database"],
            "collection": pat["collection"],
            "filter": cmd.get("filter") if op == "find" else cmd.get("query") or cmd.get("filter"),
            "sort": cmd.get("sort"),
            "projection": cmd.get("projection"),
            "pipeline": cmd.get("pipeline"),
            "limit": cmd.get("limit") or None,
        }
    )
    doc = investigation_service.run_investigation(
        db, inst, conn.get_monitored_client(inst), spec, fingerprint=fingerprint, user_id=user["_id"]
    )
    return {"plan": investigation_service.present_plan(doc, include_raw=True)}
