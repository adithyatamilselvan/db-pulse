"""Slow-query collection: profiler entries -> fingerprinted records, patterns and alerts.

Stored slow-query rows contain only the *redacted* command shape. The latest raw command for
each pattern is kept encrypted (``sample_enc``) solely so DB Pulse can re-run ``explain`` with
the real values on demand; it is never returned by the API or sent to an LLM.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from bson import json_util
from pymongo import MongoClient
from pymongo import errors as pe
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.analysis.performance_analyzer import (
    detect_latency_increase,
    resolve_threshold_ms,
    severity_for_duration,
)
from app.analysis.query_fingerprint import fingerprint_from_profile, profile_operation
from app.analysis.scan_detector import SMALL_COLLECTION_DOCS
from app.core.encryption import encrypt_secret
from app.models.enums import DEFAULT_INSTANCE_SETTINGS, SOURCE_OBSERVED
from app.services import alert_service, profiler_service
from app.services.connection_service import SYSTEM_DBS
from app.utils.redaction import redact_values
from app.utils.serialization import as_aware, utcnow
from app.utils.stats import percentile, ratio

log = logging.getLogger("dbpulse.slowquery")
_META_KEYS = {
    "lsid",
    "$db",
    "$clusterTime",
    "$readPreference",
    "txnNumber",
    "readConcern",
    "maxTimeMS",
    "cursor",
    "comment",
    "$audit",
    "apiVersion",
}


def instance_settings(instance: dict) -> dict[str, Any]:
    return {**DEFAULT_INSTANCE_SETTINGS, **instance.get("settings", {})}


_operation = profile_operation


def _clean_command(entry: dict) -> dict:
    cmd = entry.get("command") or entry.get("query") or {}
    return {k: v for k, v in cmd.items() if k not in _META_KEYS} if isinstance(cmd, dict) else {}


def _dedupe_key(entry: dict, fp: str) -> str:
    ts = as_aware(entry["ts"]).isoformat()
    raw = f"{entry.get('ns')}|{ts}|{entry.get('millis')}|{entry.get('op')}|{entry.get('queryHash')}|{fp}"
    return hashlib.sha1(raw.encode()).hexdigest()  # noqa: S324 - identity hash, not security


def process_entry(
    entry: dict, settings: dict[str, Any], *, source: str = SOURCE_OBSERVED
) -> dict[str, Any] | None:
    """Convert a profiler entry into a slow-query record, or None if below threshold."""
    ns = entry.get("ns", "")
    if "." not in ns or "ts" not in entry or "millis" not in entry:
        return None
    database, collection = ns.split(".", 1)
    op = _operation(entry)
    threshold = resolve_threshold_ms(settings, collection, op)
    duration = float(entry["millis"])
    if duration < threshold:
        return None
    fp, shape = fingerprint_from_profile(entry)
    plan_summary = entry.get("planSummary") or ""
    docs_examined = entry.get("docsExamined")
    returned = entry.get("nreturned")
    keys_examined = entry.get("keysExamined")
    clean = _clean_command(entry)
    return {
        "database": database,
        "collection": collection,
        "operation": op,
        "ts": as_aware(entry["ts"]),
        "duration_ms": duration,
        "threshold_ms": threshold,
        "severity": severity_for_duration(duration, threshold),
        "fingerprint": fp,
        "shape": shape,
        "command_redacted": redact_values(clean),
        "plan_summary": plan_summary,
        "collection_scan": "COLLSCAN" in plan_summary,
        "index_scan": any(s in plan_summary for s in ("IXSCAN", "IDHACK", "COUNT_SCAN")),
        "docs_examined": docs_examined,
        "keys_examined": keys_examined,
        "docs_returned": returned,
        "efficiency_ratio": ratio(docs_examined, returned),
        "client_app": entry.get("appName"),
        "response_length": entry.get("responseLength"),
        "query_hash": entry.get("queryHash"),
        "dedupe_key": _dedupe_key(entry, fp),
        "source": source,
        "_raw_command": clean,
    }


def _upsert_pattern(db: Database, instance: dict, rec: dict) -> dict:
    sample_enc = encrypt_secret(json_util.dumps(rec["_raw_command"]))
    pat = db.query_patterns.find_one_and_update(
        {"instance_id": instance["_id"], "fingerprint": rec["fingerprint"], "source": rec["source"]},
        {
            "$setOnInsert": {
                "owner_id": instance["owner_id"],
                "first_seen": rec["ts"],
                "shape": rec["shape"],
                "database": rec["database"],
                "collection": rec["collection"],
                "operation": rec["operation"],
            },
            "$set": {
                "last_seen": rec["ts"],
                "sample_enc": sample_enc,
                "last_plan_summary": rec["plan_summary"],
            },
            "$inc": {"count": 1, "total_ms": rec["duration_ms"]},
            "$max": {"max_ms": rec["duration_ms"]},
            "$push": {"recent_durations_ms": {"$each": [rec["duration_ms"]], "$slice": -60}},
        },
        upsert=True,
        return_document=True,
    )
    return pat


def ingest_entries(
    db: Database, instance: dict, entries: list[dict], *, source: str = SOURCE_OBSERVED
) -> dict[str, int]:
    settings = instance_settings(instance)
    summary = {
        "seen": len(entries),
        "ingested": 0,
        "below_threshold": 0,
        "duplicates": 0,
        "alerts_created": 0,
        "alerts_deduplicated": 0,
    }
    touched: dict[str, dict] = {}

    def alert(**kw: Any) -> None:
        _, created = alert_service.raise_alert(db, instance=instance, source=source, **kw)
        summary["alerts_created" if created else "alerts_deduplicated"] += 1

    for entry in entries:
        rec = process_entry(entry, settings, source=source)
        if rec is None:
            summary["below_threshold"] += 1
            continue
        raw = rec.pop("_raw_command")
        doc = {**rec, "instance_id": instance["_id"], "owner_id": instance["owner_id"]}
        try:
            db.slow_queries.insert_one(doc)
        except DuplicateKeyError:
            summary["duplicates"] += 1
            continue
        summary["ingested"] += 1
        pat = _upsert_pattern(db, instance, {**rec, "_raw_command": raw})
        touched[rec["fingerprint"]] = pat
        ctx = {
            "database": rec["database"],
            "collection": rec["collection"],
            "fingerprint": rec["fingerprint"],
        }
        evidence = {
            "duration_ms": rec["duration_ms"],
            "threshold_ms": rec["threshold_ms"],
            "docs_examined": rec["docs_examined"],
            "docs_returned": rec["docs_returned"],
            "keys_examined": rec["keys_examined"],
            "plan_summary": rec["plan_summary"],
            "operation": rec["operation"],
        }
        alert(
            alert_type="SLOW_QUERY",
            severity=rec["severity"],
            title=f"Slow {rec['operation']} on {rec['database']}.{rec['collection']}",
            message=f"Took {rec['duration_ms']:.0f} ms (threshold {rec['threshold_ms']} ms)",
            evidence=evidence,
            **ctx,
        )
        de = rec["docs_examined"] or 0
        if rec["collection_scan"] and de >= settings["collscan_alert_min_docs"]:
            alert(
                alert_type="COLLECTION_SCAN",
                severity="HIGH" if de >= 100_000 else "MEDIUM",
                title=f"Collection scan on {rec['database']}.{rec['collection']}",
                message=f"COLLSCAN examined {de} documents",
                evidence=evidence,
                **ctx,
            )
        er = rec["efficiency_ratio"]
        if er is not None and er >= settings["high_examined_ratio"] and de >= SMALL_COLLECTION_DOCS:
            alert(
                alert_type="HIGH_DOCS_EXAMINED",
                severity="MEDIUM",
                title="High documents examined vs returned",
                message=f"{de} examined for {rec['docs_returned']} returned (ratio {er:.0f})",
                evidence=evidence,
                **ctx,
            )
        k, r = rec["keys_examined"], rec["docs_returned"]
        if rec["index_scan"] and k and r and k / r >= 100:
            alert(
                alert_type="INEFFICIENT_INDEX_USAGE",
                severity="LOW",
                title="Index scanned many keys per returned document",
                message=f"{k} keys examined for {r} returned",
                evidence=evidence,
                **ctx,
            )

    for fp, pat in touched.items():
        ctx = {"database": pat["database"], "collection": pat["collection"], "fingerprint": fp}
        inc = detect_latency_increase(pat.get("recent_durations_ms", []))
        if inc:
            alert(
                alert_type="LATENCY_INCREASE",
                severity="MEDIUM",
                title="Latency increase for query pattern",
                message=f"Recent median {inc['recent_median_ms']:.0f} ms vs earlier "
                f"{inc['baseline_median_ms']:.0f} ms (+{inc['increase_pct']:.0f}%)",
                evidence=inc,
                **ctx,
            )
        window = timedelta(seconds=settings["frequency_window_s"])
        n = db.slow_queries.count_documents(
            {
                "instance_id": instance["_id"],
                "fingerprint": fp,
                "source": source,
                "ts": {"$gte": utcnow() - window},
            }
        )
        if n >= settings["frequency_alert_count"]:
            alert(
                alert_type="EXCESSIVE_QUERY_FREQUENCY",
                severity="MEDIUM",
                title="Slow query pattern repeats frequently",
                message=f"{n} slow executions in {settings['frequency_window_s']} s",
                evidence={"count": n, "window_s": settings["frequency_window_s"]},
                **ctx,
            )
    return summary


def target_databases(client: MongoClient, instance: dict) -> list[str]:
    configured = instance_settings(instance)["monitored_databases"]
    if configured:
        return list(configured)
    try:
        return [n for n in client.list_database_names() if n not in SYSTEM_DBS]
    except pe.OperationFailure:  # no listDatabases privilege: nothing to enumerate (not an outage)
        log.info("could not enumerate databases for instance %s", instance["_id"])
        return []


def collect_for_instance(db: Database, instance: dict, client: MongoClient) -> dict[str, Any]:
    """Read new profiler entries for each target database and ingest them."""
    results: dict[str, Any] = {}
    checkpoints = dict(instance.get("checkpoints", {}))
    for db_name in target_databases(client, instance):
        read = profiler_service.read_profile_entries(client, db_name, since=checkpoints.get(db_name))
        if not read["available"]:
            results[db_name] = {"available": False, "reason": read["reason"]}
            continue
        entries = read["entries"]
        summary = ingest_entries(db, instance, entries)
        if entries:
            checkpoints[db_name] = as_aware(entries[-1]["ts"])
        results[db_name] = {"available": True, **summary}
    db.instances.update_one(
        {"_id": instance["_id"]},
        {
            "$set": {
                "checkpoints": checkpoints,
                "last_collection_at": utcnow(),
                "last_collection_result": json.loads(json.dumps(results, default=str)),
            }
        },
    )
    return results


def pattern_stats(pat: dict) -> dict[str, Any]:
    durations = pat.get("recent_durations_ms", [])
    return {
        "avg_ms": (pat["total_ms"] / pat["count"]) if pat.get("count") else None,
        "max_ms": pat.get("max_ms"),
        "p95_ms": percentile(durations, 95),
        "p99_ms": percentile(durations, 99),
        "count": pat.get("count"),
    }
