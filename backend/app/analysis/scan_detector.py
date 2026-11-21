"""Turn a parsed explain into structured, evidence-backed findings.

A finding is a *heuristic*, not a verdict. Each carries the numbers it was derived from so the
UI/AI/report can show the evidence. Nuances encoded here (per the MongoDB docs):
 - a COLLSCAN is not automatically a problem (small collection, or the query matches most docs)
 - high keys examined does not by itself mean an index is ineffective
"""

from __future__ import annotations

from typing import Any

SMALL_COLLECTION_DOCS = 1000
HIGH_EXAMINED_RATIO = 10.0
LOW_SELECTIVITY_RATIO = 2.0  # examined/returned this low => the scan is "mostly useful work"


def _finding(code: str, severity: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "kind": "heuristic", "message": message, "evidence": evidence}


def detect_findings(parsed: dict[str, Any], *, collection_doc_count: int | None = None) -> list[dict]:
    """Return findings for a parsed explain. ``severity`` is one of INFO/LOW/MEDIUM/HIGH."""
    if not parsed.get("parsed_ok"):
        return [
            _finding(
                "PLAN_UNPARSEABLE",
                "INFO",
                "Explain output did not contain a recognizable plan; no plan findings",
            )
        ]
    findings: list[dict] = []
    examined = parsed.get("documents_examined")
    returned = parsed.get("documents_returned")
    keys = parsed.get("keys_examined")
    eff = parsed.get("efficiency_ratio")

    if parsed["collection_scan_detected"]:
        small = collection_doc_count is not None and collection_doc_count < SMALL_COLLECTION_DOCS
        if small:
            findings.append(
                _finding(
                    "COLLSCAN_SMALL_COLLECTION",
                    "INFO",
                    "COLLSCAN on a small collection; usually not a performance problem",
                    collection_doc_count=collection_doc_count,
                )
            )
        elif eff is not None and eff <= LOW_SELECTIVITY_RATIO and (returned or 0) > 0:
            findings.append(
                _finding(
                    "COLLSCAN_LOW_SELECTIVITY",
                    "INFO",
                    "COLLSCAN detected but most examined documents are returned (low selectivity); "
                    "an index may not help",
                    documents_examined=examined,
                    documents_returned=returned,
                )
            )
        else:
            sev = "HIGH" if (eff or 0) >= 100 or (examined or 0) >= 100_000 else "MEDIUM"
            findings.append(
                _finding(
                    "COLLSCAN",
                    sev,
                    "COLLSCAN stage detected: every document was read",
                    documents_examined=examined,
                    documents_returned=returned,
                    efficiency_ratio=eff,
                )
            )

    if parsed["index_scan_detected"] and not parsed["collection_scan_detected"]:
        findings.append(
            _finding(
                "IXSCAN",
                "INFO",
                "Index scan used: " + ", ".join(parsed["index_names"] or ["(unnamed)"]),
                index_names=parsed["index_names"],
            )
        )

    if examined is not None and returned is not None and eff is not None and eff >= HIGH_EXAMINED_RATIO:
        sev = "HIGH" if eff >= 1000 else "MEDIUM"
        findings.append(
            _finding(
                "HIGH_EXAMINED_TO_RETURNED",
                sev,
                "High documents examined relative to documents returned",
                documents_examined=examined,
                documents_returned=returned,
                efficiency_ratio=eff,
            )
        )

    if (
        parsed["index_scan_detected"]
        and keys is not None
        and returned
        and keys / max(returned, 1) >= HIGH_EXAMINED_RATIO * 10
    ):
        findings.append(
            _finding(
                "HIGH_KEYS_EXAMINED",
                "LOW",
                "Many index keys examined per returned document; the index narrows the search only "
                "partly (this alone does not prove the index is ineffective)",
                keys_examined=keys,
                documents_returned=returned,
            )
        )

    if parsed["sort_stage_detected"]:
        sev = "MEDIUM" if (examined or 0) >= 10_000 else "LOW"
        findings.append(
            _finding(
                "IN_MEMORY_SORT",
                sev,
                "Blocking SORT stage: results are sorted in memory because no index provides the order",
                documents_examined=examined,
            )
        )

    if (
        parsed["index_scan_detected"]
        and parsed["fetch_detected"]
        and examined
        and returned is not None
        and returned > 0
        and examined / returned >= HIGH_EXAMINED_RATIO
    ):
        findings.append(
            _finding(
                "FETCH_FILTERS_MANY",
                "LOW",
                "Documents are fetched then discarded by a residual filter; a more selective or "
                "compound index might narrow the index scan",
                documents_examined=examined,
                documents_returned=returned,
            )
        )

    if parsed["rejected_plans_count"]:
        findings.append(
            _finding(
                "REJECTED_PLANS",
                "INFO",
                f"Planner considered {parsed['rejected_plans_count']} rejected plan(s)",
                rejected=parsed["rejected_plans"],
            )
        )

    if not parsed["has_execution_stats"]:
        findings.append(
            _finding(
                "NO_EXECUTION_STATS",
                "INFO",
                "Only queryPlanner output available; examined/returned figures are unavailable",
            )
        )
    return findings


def max_severity(findings: list[dict]) -> str:
    order = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    best = 0
    for f in findings:
        best = max(best, order.index(f.get("severity", "INFO")))
    return order[best]


def analyze_plan(explain: dict | None, *, collection_doc_count: int | None = None) -> dict[str, Any]:
    """Convenience: parse + findings in one structured result."""
    from app.analysis.plan_parser import parse_explain

    parsed = parse_explain(explain)
    parsed["findings"] = detect_findings(parsed, collection_doc_count=collection_doc_count)
    parsed["max_severity"] = max_severity(parsed["findings"])
    parsed["finding_messages"] = [f["message"] for f in parsed["findings"]]
    return parsed
