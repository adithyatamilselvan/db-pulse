"""Request/response schemas (Pydantic)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_PASSWORD_RULE = "Password must be at least 10 characters and contain a letter and a digit"  # noqa: S105 - validation message, not a credential


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterIn(Strict):
    email: EmailStr
    password: str = Field(max_length=128)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str) -> str:
        if len(v) < 10 or not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError(_PASSWORD_RULE)
        return v


class LoginIn(Strict):
    email: EmailStr
    password: str = Field(max_length=128)


class ProfileUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    current_password: str | None = Field(default=None, max_length=128)
    new_password: str | None = Field(default=None, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strong(cls, v: str | None) -> str | None:
        if v is not None and (len(v) < 10 or not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v)):
            raise ValueError(_PASSWORD_RULE)
        return v


class UriTest(Strict):
    uri: str = Field(min_length=10, max_length=2048)


class InstanceCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    uri: str = Field(min_length=10, max_length=2048)
    allow_modifications: bool = False


class InstanceUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    uri: str | None = Field(default=None, min_length=10, max_length=2048)
    allow_modifications: bool | None = None


class InstanceSettings(Strict):
    monitoring_interval_s: int = Field(default=60, ge=5, le=86400)
    detection_interval_s: int = Field(default=60, ge=5, le=86400)
    slow_threshold_ms: int = Field(default=100, ge=1, le=3_600_000)
    collection_thresholds_ms: dict[str, int] = Field(default_factory=dict)
    operation_thresholds_ms: dict[str, int] = Field(default_factory=dict)
    alert_cooldown_s: int = Field(default=900, ge=0, le=86400 * 7)
    retention_days: int = Field(default=30, ge=1, le=365)
    monitored_databases: list[str] = Field(default_factory=list, max_length=50)
    collscan_alert_min_docs: int = Field(default=1000, ge=0)
    high_examined_ratio: float = Field(default=100.0, ge=1)
    frequency_alert_count: int = Field(default=20, ge=1)
    frequency_window_s: int = Field(default=900, ge=10)
    severity_overrides: dict[str, Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]] = Field(
        default_factory=dict
    )

    @field_validator("collection_thresholds_ms", "operation_thresholds_ms")
    @classmethod
    def _positive(cls, v: dict[str, int]) -> dict[str, int]:
        if any(x < 1 for x in v.values()):
            raise ValueError("thresholds must be >= 1 ms")
        return v


class QuerySpecIn(Strict):
    database: str
    collection: str
    operation: Literal["find", "count", "aggregate"] = "find"
    filter: dict[str, Any] | str | None = None
    projection: dict[str, Any] | str | None = None
    sort: dict[str, Any] | str | None = None
    limit: int | None = Field(default=None, ge=1)
    pipeline: list[dict[str, Any]] | str | None = None


class BenchmarkIn(QuerySpecIn):
    label: str = Field(default="run", max_length=60)
    warmup_iterations: int = Field(default=5, ge=0, le=1000)
    iterations: int = Field(default=100, ge=1)
    concurrency: int = Field(default=1, ge=1)
    duration_s: float | None = Field(default=None, gt=0)
    timeout_ms: int | None = Field(default=None, ge=100)
    confirm_high_load: bool = False


class CompareIn(Strict):
    before_id: str
    after_id: str


class IndexCreateIn(Strict):
    database: str
    collection: str
    keys: list[tuple[str, int]] = Field(min_length=1, max_length=8)
    name: str | None = Field(default=None, max_length=120)
    confirm: bool = False
    acknowledge_write_cost: bool = False


class IndexRollbackIn(Strict):
    change_id: str
    confirm: bool = False


class ProfilerSetIn(Strict):
    database: str
    level: int = Field(ge=0, le=2)
    slowms: int = Field(default=100, ge=0, le=3_600_000)
    sample_rate: float = Field(default=1.0, gt=0, le=1)
    confirm: bool = False


class ProfilerRestoreIn(Strict):
    change_id: str
    confirm: bool = False


class AIInvestigateIn(Strict):
    instance_id: str
    question: str = Field(min_length=3, max_length=1000)
    fingerprint: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, pattern="^(observed|SYNTHETIC)$")
    plan_id: str | None = None
    comparison_id: str | None = None
    conversation_id: str | None = None


class ReportIn(Strict):
    instance_id: str
    period: str = "24h"
    start: str | None = None
    end: str | None = None
    comparison_id: str | None = None
    title: str | None = Field(default=None, max_length=150)
    include_synthetic: bool | None = None
