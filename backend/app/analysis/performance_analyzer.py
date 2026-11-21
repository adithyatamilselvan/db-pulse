"""Benchmark comparison, threshold resolution and latency-trend detection (pure functions)."""

from __future__ import annotations

from typing import Any

from app.utils.stats import increase_pct, reduction_pct

# (key, label, lower_is_better, unit)
_METRICS = [
    ("avg", "Average latency", True, "ms"),
    ("median", "Median latency", True, "ms"),
    ("p95", "P95 latency", True, "ms"),
    ("p99", "P99 latency", True, "ms"),
    ("min", "Minimum latency", True, "ms"),
    ("max", "Maximum latency", True, "ms"),
]
_NOISE_PCT = 5.0  # changes inside +/-5% are reported as "unchanged"; benchmarks are noisy


def _direction(change: float | None) -> str:
    if change is None:
        return "unavailable"
    if abs(change) < _NOISE_PCT:
        return "unchanged"
    return "improved" if change > 0 else "regressed"


def _row(
    key: str, label: str, before: float | None, after: float | None, unit: str, lower_is_better: bool
) -> dict[str, Any]:
    change = reduction_pct(before, after) if lower_is_better else increase_pct(before, after)
    return {
        "key": key,
        "label": label,
        "before": before,
        "after": after,
        "unit": unit,
        "lower_is_better": lower_is_better,
        # change_pct is positive when the metric got *better*, whichever its polarity
        "change_pct": change,
        "direction": _direction(change),
    }


def compare_plans(before: dict | None, after: dict | None) -> dict[str, Any]:
    b, a = before or {}, after or {}
    return {
        "before_stages": b.get("stage_names", []),
        "after_stages": a.get("stage_names", []),
        "before_indexes": b.get("index_names", []),
        "after_indexes": a.get("index_names", []),
        "collscan_before": bool(b.get("collection_scan_detected")),
        "collscan_after": bool(a.get("collection_scan_detected")),
        "plan_changed": b.get("stage_names") != a.get("stage_names"),
        "index_usage_changed": b.get("index_names") != a.get("index_names"),
    }


def compare_runs(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two benchmark result summaries.

    Each side: ``{"latency_ms": {avg,median,p95,p99,min,max}, "throughput_ops": float,
    "failed": int, "docs_examined": int|None, "keys_examined": int|None,
    "docs_returned": int|None, "plan": parsed_plan}``.
    """
    lb, la = before.get("latency_ms", {}), after.get("latency_ms", {})
    rows = [_row(k, label, lb.get(k), la.get(k), unit, low) for k, label, low, unit in _METRICS]
    rows.append(
        _row(
            "throughput_ops",
            "Throughput",
            before.get("throughput_ops"),
            after.get("throughput_ops"),
            "ops/s",
            False,
        )
    )
    rows.append(
        _row(
            "docs_examined",
            "Documents examined",
            before.get("docs_examined"),
            after.get("docs_examined"),
            "docs",
            True,
        )
    )
    rows.append(
        _row(
            "keys_examined",
            "Keys examined",
            before.get("keys_examined"),
            after.get("keys_examined"),
            "keys",
            True,
        )
    )
    rows.append(
        _row(
            "docs_returned",
            "Documents returned",
            before.get("docs_returned"),
            after.get("docs_returned"),
            "docs",
            True,
        )
    )
    rows.append(
        _row(
            "failed",
            "Failed operations",
            before.get("failed", 0),
            after.get("failed", 0),
            "ops",
            True,
        )
    )
    # documents returned is a correctness check, not a performance goal
    for r in rows:
        if r["key"] == "docs_returned":
            r["direction"] = "unchanged" if r["before"] == r["after"] else "differs"
            r["change_pct"] = None
    plan = compare_plans(before.get("plan"), after.get("plan"))
    p95 = next(r for r in rows if r["key"] == "p95")

    caveats = [
        "Results are measurements from this environment and vary with cache state, "
        "concurrent load, hardware and data distribution."
    ]
    if plan["collscan_after"]:
        verdict = "NOT_VALIDATED"
        summary = (
            "The optimized run still uses a collection scan; the index change did not "
            "alter the access path for this query."
        )
    elif p95["direction"] == "improved":
        verdict = "IMPROVED"
        summary = f"P95 latency improved by {p95['change_pct']:.1f}% in this run."
        if plan["collscan_before"] and not plan["collscan_after"]:
            summary += " The plan changed from COLLSCAN to an index-based plan."
    elif p95["direction"] == "regressed":
        verdict = "REGRESSED"
        summary = f"P95 latency regressed by {abs(p95['change_pct']):.1f}% in this run."
    elif p95["direction"] == "unavailable":
        verdict, summary = "INCONCLUSIVE", "P95 latency was unavailable for one of the runs."
    else:
        verdict = "UNCHANGED"
        summary = f"P95 latency changed by less than {_NOISE_PCT:.0f}% (within noise)."
    if before.get("docs_returned") != after.get("docs_returned"):
        caveats.append(
            "Documents returned differ between runs; the compared queries may not be "
            "equivalent, or the data changed."
        )
    return {"rows": rows, "plan": plan, "verdict": verdict, "summary": summary, "caveats": caveats}


def resolve_threshold_ms(settings: dict[str, Any], collection: str, operation: str) -> int:
    """Operation-specific > collection-specific > default."""
    ops = settings.get("operation_thresholds_ms") or {}
    cols = settings.get("collection_thresholds_ms") or {}
    if operation in ops:
        return int(ops[operation])
    if collection in cols:
        return int(cols[collection])
    return int(settings.get("slow_threshold_ms", 100))


def severity_for_duration(duration_ms: float, threshold_ms: float) -> str:
    if threshold_ms <= 0:
        return "INFO"
    mult = duration_ms / threshold_ms
    if mult >= 20:
        return "CRITICAL"
    if mult >= 8:
        return "HIGH"
    if mult >= 3:
        return "MEDIUM"
    return "LOW"


def detect_latency_increase(
    points: list[float], *, min_points: int = 8, factor: float = 1.5
) -> dict[str, Any] | None:
    """Compare the newer half of ``points`` (oldest->newest) against the older half by median."""
    from app.utils.stats import percentile

    if len(points) < min_points:
        return None
    half = len(points) // 2
    old, new = points[:half], points[half:]
    m_old, m_new = percentile(old, 50), percentile(new, 50)
    if not m_old or m_new is None:
        return None
    if m_new / m_old >= factor:
        return {
            "baseline_median_ms": m_old,
            "recent_median_ms": m_new,
            "increase_pct": (m_new - m_old) / m_old * 100,
        }
    return None
