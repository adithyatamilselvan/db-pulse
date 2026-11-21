"""Query fingerprinting: group queries by *shape*, ignoring literal values.

Two queries with the same fields/operators/sort share a fingerprint regardless of the
values searched for, so repeated slow patterns aggregate and no sensitive values are stored.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_ORDER_INSENSITIVE = {"$and", "$or", "$nor"}


def _shape(node: Any) -> Any:
    if isinstance(node, dict):
        items = {k: _shape(v) for k, v in node.items()}
        return {k: items[k] for k in sorted(items)}
    if isinstance(node, (list, tuple)):
        if all(not isinstance(x, (dict, list, tuple)) for x in node):
            return ["?"]  # {$in: [1,2,3]} and {$in: [9]} are the same shape
        return [_shape(x) for x in node]
    return "?"


def _normalize(node: Any, parent_key: str | None = None) -> Any:
    shaped = _shape(node)
    return _sort_logical(shaped, parent_key)


def _sort_logical(node: Any, parent_key: str | None = None) -> Any:
    if isinstance(node, dict):
        return {k: _sort_logical(v, k) for k, v in node.items()}
    if isinstance(node, list):
        items = [_sort_logical(x, parent_key) for x in node]
        if parent_key in _ORDER_INSENSITIVE:
            items.sort(key=lambda x: json.dumps(x, sort_keys=True, default=str))
        return items
    return node


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]  # noqa: S324 - not security-sensitive


def query_shape(
    *,
    operation: str,
    collection: str,
    filter_: dict | None = None,
    sort: dict | list | None = None,
    projection: dict | None = None,
    pipeline: list | None = None,
) -> dict[str, Any]:
    """Return the value-free canonical shape of a query."""
    shape: dict[str, Any] = {"op": operation.lower(), "collection": collection}
    if filter_:
        shape["filter"] = _normalize(filter_)
    if sort:
        pairs = sort.items() if isinstance(sort, dict) else sort
        # sort order is significant: keep the sequence, drop nothing
        shape["sort"] = [[str(k), int(d) if isinstance(d, (int, float)) else 1] for k, d in pairs]
    if projection:
        shape["projection"] = sorted(projection.keys())
    if pipeline:
        shape["pipeline"] = [_normalize(stage) for stage in pipeline]
    return shape


def fingerprint(**kwargs: Any) -> str:
    return _hash(query_shape(**kwargs))


def profile_operation(entry: dict) -> str:
    """Normalize the profiler's ``op`` ('query', 'command', ...) to find/aggregate/count/...

    The profiler reports finds as op='query' and many commands as op='command'; the real
    operation is the first key of the recorded command document.
    """
    op = entry.get("op", "unknown")
    cmd = entry.get("command")
    if op in ("query", "command") and isinstance(cmd, dict):
        for name in ("find", "aggregate", "count", "distinct"):
            if name in cmd:
                return name
    return "find" if op == "query" else op


def fingerprint_from_profile(entry: dict) -> tuple[str, dict[str, Any]]:
    """Fingerprint a ``system.profile`` document. Returns (fingerprint, shape)."""
    ns = entry.get("ns", "")
    collection = ns.split(".", 1)[1] if "." in ns else ns
    op = profile_operation(entry)
    cmd = entry.get("command") or entry.get("query") or {}
    shape = query_shape(
        operation=op,
        collection=collection,
        filter_=cmd.get("filter") if op == "find" else cmd.get("query") or cmd.get("filter"),
        sort=cmd.get("sort"),
        projection=cmd.get("projection"),
        pipeline=cmd.get("pipeline") if op == "aggregate" else None,
    )
    return _hash(shape), shape
