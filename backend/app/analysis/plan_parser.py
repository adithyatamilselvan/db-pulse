"""Recursive, defensive parser for MongoDB ``explain`` output.

Handles: find / count explains, aggregation explains (``stages[].$cursor`` and top-level
``queryPlanner``), classic and slot-based (SBE) plan shapes (``queryPlan`` wrapper), and
sharded ``shards[]`` fan-out. Every field is optional: missing data yields ``None``, never
an exception and never an invented value.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.stats import ratio

_CHILD_KEYS = (
    "inputStage",
    "inputStages",
    "outerStage",
    "innerStage",
    "thenStage",
    "elseStage",
    "queryPlan",
    "shards",
)
_DETAIL_KEYS = (
    "indexName",
    "keyPattern",
    "direction",
    "indexBounds",
    "filter",
    "sortPattern",
    "memLimit",
    "isMultiKey",
    "isUnique",
    "isPartial",
    "isSparse",
    "limitAmount",
    "transformBy",
)
_COUNTER_KEYS = {
    "nReturned": "n_returned",
    "docsExamined": "docs_examined",
    "keysExamined": "keys_examined",
    "executionTimeMillisEstimate": "exec_time_ms_estimate",
    "works": "works",
    "seeks": "seeks",
}
_SCAN_STAGES = {"COLLSCAN"}
# Slot-based-engine (SBE) stage names that map onto the classic stages we reason about.
_SBE_CANON = {
    "scan": "COLLSCAN",
    "cscan": "COLLSCAN",
    "ixseek": "IXSCAN",
    "ixscan": "IXSCAN",
    "seek": "FETCH",
    "sort": "SORT",
}


def canonical_stage(stage: str | None) -> str:
    if not stage:
        return "UNKNOWN"
    return _SBE_CANON.get(stage, stage)


_INDEX_STAGES = {
    "IXSCAN",
    "COUNT_SCAN",
    "DISTINCT_SCAN",
    "IDHACK",
    "EXPRESS_IXSCAN",
    "EXPRESS_CLUSTERED_IXSCAN",
}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _unwrap(node: dict) -> dict:
    """Unwrap per-shard wrappers so stage detection sees the actual plan node."""
    for key in ("winningPlan", "executionStages", "queryPlan"):
        if "stage" not in node and isinstance(node.get(key), dict):
            return _unwrap(node[key])
    return node


def _parse_node(node: Any, depth: int, flat: list[dict]) -> dict | None:
    if not isinstance(node, dict):
        return None
    node = _unwrap(node)
    stage = node.get("stage")
    parsed: dict[str, Any] = {"stage": stage or "UNKNOWN", "depth": depth, "children": []}
    for key in _DETAIL_KEYS:
        if key in node:
            parsed[_snake(key)] = node[key]
    for src, dst in _COUNTER_KEYS.items():
        if src in node:
            parsed[dst] = _as_int(node[src])
    if stage:
        flat.append(parsed)
    child_depth = depth + 1 if stage else depth
    for key in _CHILD_KEYS:
        child = node.get(key)
        if isinstance(child, dict):
            sub = _parse_node(child, child_depth, flat)
            if sub:
                parsed["children"].append(sub)
        elif isinstance(child, list):
            for item in child:
                sub = _parse_node(item, child_depth, flat)
                if sub:
                    parsed["children"].append(sub)
    # a wrapper node with no "stage" (e.g. queryPlan holder) collapses into its only child
    if not stage and len(parsed["children"]) == 1:
        return parsed["children"][0]
    return parsed if stage or parsed["children"] else None


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _find_sections(explain: dict) -> tuple[dict | None, dict | None]:
    """Locate (queryPlanner, executionStats) in find/count/aggregate explain shapes."""
    if isinstance(explain.get("queryPlanner"), dict):
        return explain["queryPlanner"], explain.get("executionStats")
    stages = explain.get("stages")
    if isinstance(stages, list):
        for st in stages:
            cur = st.get("$cursor") if isinstance(st, dict) else None
            if isinstance(cur, dict) and isinstance(cur.get("queryPlanner"), dict):
                return cur["queryPlanner"], cur.get("executionStats")
    shards = explain.get("shards")
    if isinstance(shards, dict):
        for sh in shards.values():
            if isinstance(sh, dict):
                qp, es = _find_sections(sh)
                if qp:
                    return qp, es
    return None, None


def _plan_summary(flat: list[dict]) -> str:
    parts = []
    for st in flat:
        if st["stage"] in _SCAN_STAGES:
            parts.append("COLLSCAN")
        elif st["stage"] in _INDEX_STAGES:
            kp = st.get("key_pattern")
            parts.append(f"{st['stage']} {dict(kp)}" if isinstance(kp, dict) else st["stage"])
    return ", ".join(parts)


def parse_explain(explain: dict | None) -> dict[str, Any]:
    """Parse an explain document into a structured, JSON-safe analysis (no findings yet)."""
    result: dict[str, Any] = {
        "parsed_ok": False,
        "namespace": None,
        "tree": None,
        "stages": [],
        "stage_names": [],
        "collection_scan_detected": False,
        "index_scan_detected": False,
        "fetch_detected": False,
        "sort_stage_detected": False,
        "index_names": [],
        "documents_examined": None,
        "documents_returned": None,
        "keys_examined": None,
        "execution_time_ms": None,
        "efficiency_ratio": None,
        "rejected_plans_count": 0,
        "rejected_plans": [],
        "plan_summary": "",
        "has_execution_stats": False,
        "engine": None,
    }
    if not isinstance(explain, dict):
        return result
    qp, es = _find_sections(explain)
    if qp is None:
        return result

    result["namespace"] = qp.get("namespace")
    flat: list[dict] = []
    exec_stages = es.get("executionStages") if isinstance(es, dict) else None
    winning = qp.get("winningPlan")
    tree = None
    if isinstance(exec_stages, dict):
        tree = _parse_node(exec_stages, 0, flat)
    if tree is None and isinstance(winning, dict):
        flat.clear()
        tree = _parse_node(winning, 0, flat)
    if tree is None:
        return result

    result["parsed_ok"] = True
    result["tree"] = tree
    result["stages"] = [{k: v for k, v in s.items() if k != "children"} for s in flat]
    names = [canonical_stage(s["stage"]) for s in flat]
    for s, n in zip(flat, names, strict=True):
        if s["stage"] != n:
            s["raw_stage"] = s["stage"]  # keep the engine's own name for display
        s["stage"] = n  # nodes are shared with the tree, so it is canonical too
    result["stages"] = [{k: v for k, v in s.items() if k != "children"} for s in flat]
    result["stage_names"] = names
    result["collection_scan_detected"] = any(n in _SCAN_STAGES for n in names)
    result["index_scan_detected"] = any(n in _INDEX_STAGES for n in names)
    result["fetch_detected"] = "FETCH" in names
    result["sort_stage_detected"] = any(n in ("SORT", "SORT_KEY_GENERATOR") for n in names)
    result["index_names"] = sorted({s["index_name"] for s in flat if s.get("index_name")})
    result["plan_summary"] = _plan_summary(flat)
    result["engine"] = "sbe" if qp.get("winningPlan", {}).get("slotBasedPlan") else "classic"

    rejected = qp.get("rejectedPlans")
    if isinstance(rejected, list):
        result["rejected_plans_count"] = len(rejected)
        for rp in rejected:
            rflat: list[dict] = []
            _parse_node(rp if isinstance(rp, dict) else {}, 0, rflat)
            result["rejected_plans"].append(
                {"stages": [s["stage"] for s in rflat], "summary": _plan_summary(rflat)}
            )

    if isinstance(es, dict):
        result["has_execution_stats"] = True
        result["documents_examined"] = _as_int(es.get("totalDocsExamined"))
        result["documents_returned"] = _as_int(es.get("nReturned"))
        result["keys_examined"] = _as_int(es.get("totalKeysExamined"))
        result["execution_time_ms"] = _as_int(es.get("executionTimeMillis"))
        if result["execution_time_ms"] is None and isinstance(exec_stages, dict):
            result["execution_time_ms"] = _as_int(exec_stages.get("executionTimeMillisEstimate"))
        # Aggregation/cursor explains sometimes omit totals: derive from stage counters, but
        # only where the counters actually exist (never fabricate).
        if result["documents_examined"] is None:
            vals = [s["docs_examined"] for s in flat if s.get("docs_examined") is not None]
            result["documents_examined"] = sum(vals) if vals else None
        if result["keys_examined"] is None:
            vals = [s["keys_examined"] for s in flat if s.get("keys_examined") is not None]
            result["keys_examined"] = sum(vals) if vals else None
        if result["documents_returned"] is None and tree.get("n_returned") is not None:
            result["documents_returned"] = tree["n_returned"]
        result["efficiency_ratio"] = ratio(result["documents_examined"], result["documents_returned"])
    return result


def parse_plan_summary_string(summary: str | None) -> dict[str, Any]:
    """Parse the ``planSummary`` string from system.profile (e.g. ``IXSCAN { a: 1 }``)."""
    summary = summary or ""
    return {
        "collection_scan_detected": "COLLSCAN" in summary,
        "index_scan_detected": any(s in summary for s in ("IXSCAN", "IDHACK", "COUNT_SCAN")),
        "plan_summary": summary,
    }
