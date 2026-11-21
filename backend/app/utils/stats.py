"""Latency statistics and comparison maths (pure functions, no I/O)."""

from __future__ import annotations

import math
from collections.abc import Sequence


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolation percentile (matches numpy's default). p in [0, 100]."""
    if not values:
        return None
    if not 0 <= p <= 100:
        raise ValueError("p must be within [0, 100]")
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    rank = (p / 100) * (len(data) - 1)
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return float(data[lo])
    return float(data[lo] + (data[hi] - data[lo]) * (rank - lo))


def summarize_latencies(latencies_ms: Sequence[float]) -> dict[str, float | None]:
    if not latencies_ms:
        return dict.fromkeys(("avg", "median", "p95", "p99", "min", "max"))
    return {
        "avg": sum(latencies_ms) / len(latencies_ms),
        "median": percentile(latencies_ms, 50),
        "p95": percentile(latencies_ms, 95),
        "p99": percentile(latencies_ms, 99),
        "min": float(min(latencies_ms)),
        "max": float(max(latencies_ms)),
    }


def reduction_pct(before: float | None, after: float | None) -> float | None:
    """Percentage *reduction* of a lower-is-better metric (latency, docs examined).

    1000 ms -> 200 ms gives 80.0. A regression gives a negative number.
    """
    if before is None or after is None or before == 0:
        return None
    return (before - after) / before * 100


def increase_pct(before: float | None, after: float | None) -> float | None:
    """Percentage *increase* of a higher-is-better metric (throughput).

    Kept separate from reduction_pct on purpose: a latency reduction is not a throughput gain.
    """
    if before is None or after is None or before == 0:
        return None
    return (after - before) / before * 100


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None if numerator == 0 else float(numerator)  # all examined, none returned
    return numerator / denominator
