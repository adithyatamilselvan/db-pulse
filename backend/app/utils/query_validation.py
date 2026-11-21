"""Validation of user-supplied query specifications.

Used by the explain and benchmark paths. The goals are: read-only, no server-side JavaScript,
bounded complexity, bounded result size. User input is *data*: it is parsed as (extended) JSON,
never evaluated.
"""

from __future__ import annotations

import json
from typing import Any

from bson import json_util

from app.core.exceptions import ValidationFailed

FORBIDDEN_OPERATORS = {
    "$where",
    "$function",
    "$accumulator",  # server-side JavaScript
    "$out",
    "$merge",  # writes
    "$currentOp",
    "$listSessions",
    "$listLocalSessions",
    "$planCacheStats",
    "$collStats",
    "$indexStats",
    "$changeStream",
    "$graphLookup",
    "$unionWith",
    "$lookup",
    "$facet",
}
ALLOWED_STAGES = {
    "$match",
    "$project",
    "$sort",
    "$limit",
    "$skip",
    "$group",
    "$count",
    "$unwind",
    "$addFields",
    "$set",
    "$unset",
    "$sample",
    "$sortByCount",
    "$replaceRoot",
    "$replaceWith",
}
MAX_DEPTH = 12
MAX_JSON_CHARS = 20_000
MAX_PIPELINE_STAGES = 12
MAX_RESULT_DOCS = 10_000
OPERATIONS = ("find", "count", "aggregate")


def parse_json_field(value: Any, name: str) -> Any:
    """Accept a dict/list already parsed, or a JSON (MongoDB extended JSON) string."""
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_JSON_CHARS:
            raise ValidationFailed(f"{name} is too large")
        if not value.strip():
            return None
        try:
            return json_util.loads(value)
        except (ValueError, TypeError) as exc:
            raise ValidationFailed(f"{name} is not valid JSON: {exc}") from exc
    raise ValidationFailed(f"{name} must be a JSON object or array")


def _walk(node: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValidationFailed(f"Query nesting exceeds the maximum depth of {MAX_DEPTH}")
    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                raise ValidationFailed("Query keys must be strings")
            if k in FORBIDDEN_OPERATORS:
                raise ValidationFailed(f"Operator {k} is not allowed (read-only, no server-side JS)")
            _walk(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk(v, depth + 1)


def _check_size(obj: Any, name: str) -> None:
    try:
        size = len(json_util.dumps(obj))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{name} cannot be serialized") from exc
    if size > MAX_JSON_CHARS:
        raise ValidationFailed(f"{name} exceeds the {MAX_JSON_CHARS} character limit")


def validate_query_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized, validated copy of ``spec`` or raise ValidationFailed."""
    op = spec.get("operation", "find")
    if op not in OPERATIONS:
        raise ValidationFailed(f"operation must be one of {OPERATIONS}")
    for key in ("database", "collection"):
        v = spec.get(key)
        if not isinstance(v, str) or not v or len(v) > 120 or "\x00" in v or v.startswith("system."):
            raise ValidationFailed(f"'{key}' is required and must be a valid name")
    if spec["database"] in ("admin", "local", "config"):
        raise ValidationFailed("Querying admin/local/config databases is not allowed")

    out: dict[str, Any] = {"operation": op, "database": spec["database"], "collection": spec["collection"]}
    filter_ = parse_json_field(spec.get("filter"), "filter") or {}
    if not isinstance(filter_, dict):
        raise ValidationFailed("filter must be a JSON object")
    out["filter"] = filter_
    projection = parse_json_field(spec.get("projection"), "projection")
    if projection is not None and not isinstance(projection, dict):
        raise ValidationFailed("projection must be a JSON object")
    sort = parse_json_field(spec.get("sort"), "sort")
    if sort is not None:
        if not isinstance(sort, dict):
            raise ValidationFailed('sort must be a JSON object like {"field": 1}')
        for k, d in sort.items():
            if d not in (1, -1):
                raise ValidationFailed(f"sort direction for '{k}' must be 1 or -1")
    out["projection"], out["sort"] = projection, sort

    limit = spec.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValidationFailed("limit must be a positive integer")
    out["limit"] = min(limit, MAX_RESULT_DOCS) if limit else None
    out["limit_capped"] = bool(limit and limit > MAX_RESULT_DOCS)

    pipeline = parse_json_field(spec.get("pipeline"), "pipeline")
    if op == "aggregate":
        if not isinstance(pipeline, list) or not pipeline:
            raise ValidationFailed("aggregate requires a non-empty 'pipeline' array")
        if len(pipeline) > MAX_PIPELINE_STAGES:
            raise ValidationFailed(f"pipeline exceeds {MAX_PIPELINE_STAGES} stages")
        for stage in pipeline:
            if not isinstance(stage, dict) or len(stage) != 1:
                raise ValidationFailed("each pipeline stage must be an object with one operator")
            name = next(iter(stage))
            if name not in ALLOWED_STAGES:
                raise ValidationFailed(f"Pipeline stage {name} is not allowed")
        out["pipeline"] = pipeline
    else:
        out["pipeline"] = None

    for name in ("filter", "projection", "sort", "pipeline"):
        if out.get(name):
            _walk(out[name])
            _check_size(out[name], name)
    return out


def spec_to_jsonable(spec: dict[str, Any]) -> dict[str, Any]:
    """Round-trip through extended JSON so ObjectIds/dates survive BSON-unfriendly paths."""
    return json.loads(json_util.dumps(spec))
