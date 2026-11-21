"""Run real ``explain`` against a monitored instance and analyze the result."""

from __future__ import annotations

import logging
from typing import Any

from pymongo import MongoClient
from pymongo import errors as pe

from app.analysis.scan_detector import analyze_plan
from app.config import get_settings
from app.core.exceptions import NotFoundError, UnsupportedOperation
from app.services.connection_service import classify_error
from app.utils.query_validation import MAX_RESULT_DOCS

log = logging.getLogger("dbpulse.explain")


def build_command(spec: dict[str, Any], *, max_time_ms: int) -> dict[str, Any]:
    """Translate a validated spec into the inner (explainable) command document."""
    coll = spec["collection"]
    op = spec["operation"]
    if op == "find":
        cmd: dict[str, Any] = {"find": coll, "filter": spec.get("filter") or {}}
        if spec.get("limit"):
            cmd["limit"] = min(spec["limit"], MAX_RESULT_DOCS)
        if spec.get("projection"):
            cmd["projection"] = spec["projection"]
        if spec.get("sort"):
            cmd["sort"] = spec["sort"]
    elif op == "count":
        cmd = {"count": coll, "query": spec.get("filter") or {}}
    else:
        cmd = {"aggregate": coll, "pipeline": spec["pipeline"], "cursor": {}}
    cmd["maxTimeMS"] = max_time_ms
    return cmd


def collection_exists(client: MongoClient, db_name: str, coll: str) -> bool:
    return bool(client[db_name].list_collection_names(filter={"name": coll}))


def run_explain(client: MongoClient, spec: dict[str, Any]) -> dict[str, Any]:
    """Execute ``explain(executionStats)``. NOTE: executionStats runs the query (read-only)."""
    s = get_settings()
    db_name, coll = spec["database"], spec["collection"]
    try:
        if not collection_exists(client, db_name, coll):
            raise NotFoundError(f"Collection '{db_name}.{coll}' does not exist")
        inner = build_command(spec, max_time_ms=s.benchmark_op_timeout_ms)
        return client[db_name].command({"explain": inner, "verbosity": "executionStats"})
    except NotFoundError:
        raise
    except pe.PyMongoError as exc:
        code, msg = classify_error(exc)
        raise UnsupportedOperation(f"Explain failed ({code}): {msg}") from exc


def explain_and_analyze(client: MongoClient, spec: dict[str, Any]) -> dict[str, Any]:
    """Explain + parse + findings. The raw explain is kept so nothing is fabricated or lost."""
    raw = run_explain(client, spec)
    try:
        doc_count = client[spec["database"]][spec["collection"]].estimated_document_count()
    except pe.PyMongoError:
        doc_count = None
    analysis = analyze_plan(raw, collection_doc_count=doc_count)
    analysis["collection_document_count"] = doc_count
    return {"raw_explain": raw, "analysis": analysis}
