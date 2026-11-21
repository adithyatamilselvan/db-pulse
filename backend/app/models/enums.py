"""Shared constants. Plain string constants keep BSON documents simple and portable."""

from __future__ import annotations

SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

ALERT_TYPES = (
    "SLOW_QUERY",
    "COLLECTION_SCAN",
    "LATENCY_INCREASE",
    "HIGH_DOCS_EXAMINED",
    "EXCESSIVE_QUERY_FREQUENCY",
    "INEFFICIENT_INDEX_USAGE",
    "CONNECTION_FAILURE",
    "MONITORING_FAILURE",
    "BENCHMARK_REGRESSION",
)
ALERT_STATUSES = ("open", "acknowledged", "resolved")

ROLES = ("admin", "user")

# Record provenance. Anything not "observed" must never be shown as a real measurement.
SOURCE_OBSERVED = "observed"
SOURCE_SYNTHETIC = "SYNTHETIC"

DEFAULT_INSTANCE_SETTINGS: dict = {
    "monitoring_interval_s": 60,
    "detection_interval_s": 60,
    "slow_threshold_ms": 100,
    "collection_thresholds_ms": {},
    "operation_thresholds_ms": {},
    "alert_cooldown_s": 900,
    "retention_days": 30,
    "monitored_databases": [],  # empty => all non-system databases
    "collscan_alert_min_docs": 1000,
    "high_examined_ratio": 100.0,
    "frequency_alert_count": 20,
    "frequency_window_s": 900,
    "severity_overrides": {},  # {"COLLECTION_SCAN": "CRITICAL"}
}
