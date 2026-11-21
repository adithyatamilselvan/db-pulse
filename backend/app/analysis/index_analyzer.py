"""Static analysis of a collection's existing indexes (duplicates, redundant prefixes, unused).

Operates on ``listIndexes`` output plus optional ``$indexStats`` output. Pure functions.
"""

from __future__ import annotations

from typing import Any

CAVEAT_UNUSED = (
    "$indexStats counters are per-instance and reset on server restart; zero accesses does not "
    "prove an index is safe to drop (it may serve rare reports, constraints, or other nodes)."
)


def _keys(idx: dict) -> list[tuple[str, Any]]:
    return list(idx.get("key", {}).items())


def _options_signature(idx: dict) -> tuple:
    """Options that change index semantics; indexes differing here are *not* interchangeable."""
    return (
        bool(idx.get("unique")),
        bool(idx.get("sparse")),
        repr(idx.get("partialFilterExpression")),
        repr(idx.get("collation")),
        idx.get("expireAfterSeconds"),
    )


def find_duplicates(indexes: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for idx in indexes:
        sig = (tuple(_keys(idx)), _options_signature(idx))
        if sig in seen:
            out.append(
                {
                    "type": "duplicate",
                    "severity": "MEDIUM",
                    "indexes": [seen[sig]["name"], idx["name"]],
                    "message": f"Indexes '{seen[sig]['name']}' and '{idx['name']}' have identical keys "
                    "and options; one is redundant",
                    "evidence": {"key": dict(idx["key"])},
                }
            )
        else:
            seen[sig] = idx
    return out


def find_redundant_prefixes(indexes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for a in indexes:
        if a.get("name") == "_id_" or a.get("unique") or a.get("expireAfterSeconds") is not None:
            continue  # constraint-bearing / TTL indexes are never "just a prefix"
        ka = _keys(a)
        if not ka or any(isinstance(d, str) for _, d in ka):  # text / hashed / 2dsphere
            continue
        for b in indexes:
            if a is b or a.get("name") == b.get("name"):
                continue
            kb = _keys(b)
            if len(kb) > len(ka) and kb[: len(ka)] == ka and _options_signature(a) == _options_signature(b):
                out.append(
                    {
                        "type": "redundant_prefix",
                        "severity": "LOW",
                        "indexes": [a["name"], b["name"]],
                        "message": f"Index '{a['name']}' {dict(a['key'])} is a prefix of "
                        f"'{b['name']}' {dict(b['key'])}; queries it serves can use the "
                        "longer index (it costs extra writes and storage)",
                        "evidence": {"redundant": dict(a["key"]), "covering": dict(b["key"])},
                    }
                )
                break
    return out


def find_unused(indexes: list[dict], index_stats: list[dict] | None) -> list[dict]:
    if not index_stats:
        return []
    ops = {s["name"]: s for s in index_stats if "name" in s}
    out: list[dict] = []
    by_name = {i["name"]: i for i in indexes}
    for name, st in ops.items():
        idx = by_name.get(name)
        if not idx or name == "_id_" or idx.get("unique") or idx.get("expireAfterSeconds") is not None:
            continue
        accesses = st.get("accesses", {}) or {}
        n = accesses.get("ops")
        if n == 0:
            out.append(
                {
                    "type": "potentially_unused",
                    "severity": "LOW",
                    "indexes": [name],
                    "message": f"Index '{name}' recorded 0 accesses since {accesses.get('since')}. "
                    + CAVEAT_UNUSED,
                    "evidence": {"accesses_ops": 0, "since": accesses.get("since")},
                }
            )
    return out


def analyze_indexes(indexes: list[dict], index_stats: list[dict] | None = None) -> dict[str, Any]:
    """Full static analysis: returns issues plus a per-index summary table."""
    issues = find_duplicates(indexes) + find_redundant_prefixes(indexes) + find_unused(indexes, index_stats)
    stats_by_name = {s.get("name"): s for s in (index_stats or [])}
    summary = []
    for idx in indexes:
        st = stats_by_name.get(idx["name"])
        acc = (st or {}).get("accesses") or {}
        summary.append(
            {
                "name": idx["name"],
                "key": dict(idx["key"]),
                "unique": bool(idx.get("unique")),
                "sparse": bool(idx.get("sparse")),
                "partial": idx.get("partialFilterExpression") is not None,
                "ttl": idx.get("expireAfterSeconds") is not None,
                "accesses": acc.get("ops"),  # None => unavailable (not zero)
                "accesses_since": acc.get("since"),
                "size_bytes": idx.get("size_bytes"),  # filled by the service from collStats
            }
        )
    return {
        "indexes": summary,
        "issues": issues,
        "index_stats_available": index_stats is not None,
        "caveats": [CAVEAT_UNUSED],
    }
