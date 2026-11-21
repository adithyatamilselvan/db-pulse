"""Orchestrates an investigation: explain -> plan analysis -> index analysis -> recommendation."""

from __future__ import annotations

from typing import Any

from bson import json_util
from pymongo import MongoClient
from pymongo.database import Database

from app.analysis.query_fingerprint import fingerprint as make_fingerprint
from app.analysis.recommendation_engine import classify_predicates, recommend_index
from app.core.encryption import decrypt_secret, encrypt_secret
from app.services import explain_service, index_analyzer
from app.utils.redaction import redact_values
from app.utils.serialization import to_jsonable, utcnow


def _sanitize_tree(node: dict | None) -> None:
    """Redact literal values inside stage filters before persisting the analysis."""
    if not node:
        return
    if "filter" in node:
        node["filter"] = redact_values(node["filter"])
    for child in node.get("children", []):
        _sanitize_tree(child)


def sanitize_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    _sanitize_tree(analysis.get("tree"))
    for st in analysis.get("stages", []):
        if "filter" in st:
            st["filter"] = redact_values(st["filter"])
    return analysis


def spec_fingerprint(spec: dict[str, Any]) -> str:
    return make_fingerprint(
        operation=spec["operation"],
        collection=spec["collection"],
        filter_=spec.get("filter"),
        sort=spec.get("sort"),
        projection=spec.get("projection"),
        pipeline=spec.get("pipeline"),
    )


def run_investigation(
    internal: Database,
    instance: dict,
    client: MongoClient,
    spec: dict[str, Any],
    *,
    fingerprint: str | None = None,
    user_id: Any = None,
    store: bool = True,
) -> dict[str, Any]:
    db_name, coll = spec["database"], spec["collection"]
    result = explain_service.explain_and_analyze(client, spec)
    analysis = sanitize_analysis(result["analysis"])
    fp = fingerprint or spec_fingerprint(spec)
    indexes = index_analyzer.list_indexes(client, db_name, coll)
    idx_inv = index_analyzer.investigate_indexes(client, db_name, coll)
    preds = classify_predicates(spec.get("filter"))
    cardinality = index_analyzer.estimate_cardinality(client, db_name, coll, preds["equality"])
    pattern = internal.query_patterns.find_one(
        {"instance_id": instance["_id"], "fingerprint": fp, "source": "observed"}
    )
    recommendation = recommend_index(
        collection=coll,
        filter_=spec.get("filter"),
        sort=spec.get("sort"),
        existing_indexes=indexes,
        plan=analysis,
        frequency=(pattern or {}).get("count"),
        cardinality=cardinality or None,
    )
    now = utcnow()
    doc = {
        "instance_id": instance["_id"],
        "owner_id": instance["owner_id"],
        "user_id": user_id,
        "target": f"{db_name}.{coll}",
        "database": db_name,
        "collection": coll,
        "operation": spec["operation"],
        "fingerprint": fp,
        "spec_redacted": to_jsonable(
            redact_values({k: spec.get(k) for k in ("filter", "sort", "projection", "pipeline")})
        ),
        "analysis": analysis,
        "recommendation": recommendation,
        "index_analysis": idx_inv,
        "raw_explain_enc": encrypt_secret(json_util.dumps(result["raw_explain"])),
        "source": "observed",
        "created_at": now,
    }
    if store:
        doc["_id"] = internal.execution_plans.insert_one(doc).inserted_id
        internal.index_analyses.insert_one(
            {
                "instance_id": instance["_id"],
                "owner_id": instance["owner_id"],
                "database": db_name,
                "collection": coll,
                "issues": idx_inv["issues"],
                "indexes": idx_inv["indexes"],
                "source": "observed",
                "created_at": now,
            }
        )
    return doc


def present_plan(doc: dict[str, Any], *, include_raw: bool) -> dict[str, Any]:
    out = {k: v for k, v in doc.items() if k != "raw_explain_enc"}
    if include_raw and doc.get("raw_explain_enc"):
        out["raw_explain"] = json_util.loads(decrypt_secret(doc["raw_explain_enc"]))
    return to_jsonable(out)
