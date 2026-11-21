from __future__ import annotations

import numpy as np
import pytest

from app.analysis.performance_analyzer import (
    compare_runs,
    detect_latency_increase,
    resolve_threshold_ms,
    severity_for_duration,
)
from app.utils.stats import increase_pct, percentile, ratio, reduction_pct, summarize_latencies


@pytest.mark.parametrize("p", [0, 1, 25, 50, 90, 95, 99, 100])
def test_percentile_matches_numpy(p):
    data = [3.2, 9.1, 1.0, 55.0, 12.5, 7.7, 100.0, 4.4, 2.2, 8.8, 15.0]
    assert percentile(data, p) == pytest.approx(float(np.percentile(data, p)))


def test_percentile_edge_cases():
    assert percentile([], 95) is None
    assert percentile([5], 99) == 5.0
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_summary_values():
    s = summarize_latencies([10, 20, 30, 40, 50])
    assert s["avg"] == 30 and s["median"] == 30 and s["min"] == 10 and s["max"] == 50
    assert summarize_latencies([])["p95"] is None


def test_latency_reduction_1000_to_200_is_80_percent():
    assert reduction_pct(1000, 200) == pytest.approx(80.0)
    assert reduction_pct(200, 1000) == pytest.approx(-400.0)  # a regression is negative
    assert reduction_pct(0, 5) is None and reduction_pct(None, 5) is None


def test_throughput_uses_increase_not_reduction():
    assert increase_pct(100, 500) == pytest.approx(400.0)
    assert reduction_pct(100, 500) != increase_pct(100, 500)


def test_ratio_semantics():
    assert ratio(100, 4) == 25 and ratio(0, 0) is None and ratio(50, 0) == 50 and ratio(None, 1) is None


def side(avg, p95, tp, docs, stages, returned=24, failed=0):
    return {
        "latency_ms": {"avg": avg, "median": avg, "p95": p95, "p99": p95, "min": 1, "max": p95},
        "throughput_ops": tp,
        "failed": failed,
        "docs_examined": docs,
        "keys_examined": 0 if docs > 100 else docs,
        "docs_returned": returned,
        "plan": {
            "stage_names": stages,
            "index_names": [] if "COLLSCAN" in stages else ["i"],
            "collection_scan_detected": "COLLSCAN" in stages,
        },
    }


def test_compare_improvement_uses_correct_polarity():
    c = compare_runs(side(1000, 1200, 1, 100000, ["COLLSCAN"]), side(200, 240, 5, 24, ["FETCH", "IXSCAN"]))
    rows = {r["key"]: r for r in c["rows"]}
    assert rows["avg"]["change_pct"] == pytest.approx(80.0) and rows["avg"]["direction"] == "improved"
    assert rows["throughput_ops"]["change_pct"] == pytest.approx(400.0)
    assert rows["docs_examined"]["direction"] == "improved"
    assert c["verdict"] == "IMPROVED" and c["plan"]["plan_changed"]
    assert "COLLSCAN to an index" in c["summary"]


def test_still_collscan_is_not_validated_even_if_faster():
    c = compare_runs(side(100, 120, 10, 100000, ["COLLSCAN"]), side(60, 70, 16, 100000, ["COLLSCAN"]))
    assert c["verdict"] == "NOT_VALIDATED"


def test_regression_and_noise_band():
    assert (
        compare_runs(side(10, 12, 100, 5, ["IXSCAN"]), side(30, 36, 33, 5, ["IXSCAN"]))["verdict"]
        == "REGRESSED"
    )
    assert (
        compare_runs(side(10, 12, 100, 5, ["IXSCAN"]), side(10.2, 12.3, 99, 5, ["IXSCAN"]))["verdict"]
        == "UNCHANGED"
    )


def test_differing_returned_counts_add_caveat():
    c = compare_runs(side(10, 12, 100, 5, ["IXSCAN"], returned=5), side(5, 6, 200, 5, ["IXSCAN"], returned=7))
    assert any("Documents returned differ" in x for x in c["caveats"])


def test_threshold_resolution_precedence():
    s = {
        "slow_threshold_ms": 100,
        "collection_thresholds_ms": {"orders": 50},
        "operation_thresholds_ms": {"aggregate": 500},
    }
    assert resolve_threshold_ms(s, "orders", "find") == 50
    assert resolve_threshold_ms(s, "orders", "aggregate") == 500  # operation beats collection
    assert resolve_threshold_ms(s, "users", "find") == 100


def test_severity_scales_with_threshold_multiple():
    assert [severity_for_duration(m, 100) for m in (100, 300, 800, 2000)] == [
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    ]


def test_latency_increase_detection():
    assert detect_latency_increase([10] * 6 + [30] * 6)["increase_pct"] == pytest.approx(200.0)
    assert detect_latency_increase([10] * 12) is None
    assert detect_latency_increase([10, 30]) is None  # too few points
