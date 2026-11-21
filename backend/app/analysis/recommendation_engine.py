"""Evidence-based index recommendations using the Equality-Sort-Range (ESR) guideline.

Output is always labelled as an *unverified hypothesis* until a benchmark validates it.
Nothing here touches a database: it is a pure function of the query shape, the existing
indexes and the observed plan metrics.
"""

from __future__ import annotations

from typing import Any

_RANGE_OPS = {"$gt", "$gte", "$lt", "$lte"}
_EQ_OPS = {"$eq", "$in"}
_NON_INDEXABLE_OPS = {"$ne", "$nin", "$not", "$where", "$exists_false"}
_MAX_IN_FOR_EQUALITY = 200


def classify_predicates(filter_: dict | None) -> dict[str, Any]:
    """Split a filter into equality / range fields; note constructs that block a simple index."""
    equality: list[str] = []
    ranges: list[str] = []
    notes: list[str] = []

    def visit(f: dict, in_or: bool = False) -> None:
        for field, cond in f.items():
            if field in ("$and",):
                for sub in cond:
                    if isinstance(sub, dict):
                        visit(sub, in_or)
                continue
            if field in ("$or", "$nor"):
                notes.append(
                    f"{field} present: each branch needs its own supporting index; "
                    "a single compound index cannot serve it"
                )
                continue
            if field.startswith("$"):
                notes.append(f"top-level operator {field} was not considered for index keys")
                continue
            if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
                ops = set(cond)
                if ops & _RANGE_OPS:
                    ranges.append(field)
                elif ops & {"$regex"}:
                    ranges.append(field)
                    notes.append(f"regex on '{field}' only uses an index if anchored (^prefix)")
                elif ops <= _EQ_OPS | {"$options"}:
                    in_list = cond.get("$in")
                    if isinstance(in_list, list) and len(in_list) > _MAX_IN_FOR_EQUALITY:
                        ranges.append(field)
                    else:
                        equality.append(field)
                elif ops & _NON_INDEXABLE_OPS:
                    notes.append(
                        f"'{field}' uses {sorted(ops & _NON_INDEXABLE_OPS)}: negation operators "
                        "are poorly supported by indexes, so it is left out of the key"
                    )
                else:
                    notes.append(f"'{field}' uses operators {sorted(ops)} not modelled; skipped")
            else:
                equality.append(field)

    visit(filter_ or {})
    dedup = lambda xs: list(dict.fromkeys(xs))  # noqa: E731 - tiny local helper
    return {
        "equality": dedup(equality),
        "range": [r for r in dedup(ranges) if r not in equality],
        "notes": notes,
    }


def _sort_fields(sort: dict | list | None) -> list[tuple[str, int]]:
    if not sort:
        return []
    pairs = sort.items() if isinstance(sort, dict) else sort
    out = []
    for k, d in pairs:
        if isinstance(d, dict):  # {$meta: ...} text-score sort: not index-orderable
            continue
        out.append((str(k), -1 if (isinstance(d, (int, float)) and d < 0) else 1))
    return out


def build_index_keys(
    equality: list[str],
    sort: list[tuple[str, int]],
    ranges: list[str],
    cardinality: dict[str, int] | None = None,
) -> list[tuple[str, int]]:
    eq = list(equality)
    if cardinality:  # more selective equality fields first (prefix reuse + earlier narrowing)
        eq.sort(key=lambda f: -cardinality.get(f, 0))
    keys: list[tuple[str, int]] = [(f, 1) for f in eq]
    seen = set(eq)
    for f, d in sort:
        if f not in seen:
            keys.append((f, d))
            seen.add(f)
    for f in ranges:
        if f not in seen:
            keys.append((f, 1))
            seen.add(f)
    return keys


def _existing_support(
    existing: list[dict],
    needed: list[tuple[str, int]],
    equality: list[str],
    sort: list[tuple[str, int]],
    ranges: list[str],
) -> dict[str, Any]:
    """Does any existing index already serve this query shape per ESR?"""
    eq_set = set(equality)
    best: dict[str, Any] = {"full": None, "partial": None, "partial_prefix_len": 0}
    for idx in existing:
        keys = list(idx.get("key", {}).items())
        fields = [k for k, _ in keys]
        if not fields or any(isinstance(d, str) for _, d in keys):
            continue
        # leading equality fields (any order) then sort then range
        i = 0
        while i < len(fields) and fields[i] in eq_set:
            i += 1
        eq_covered = i
        rest = fields[i:]
        sort_names = [f for f, _ in sort if f not in eq_set]
        sort_ok = rest[: len(sort_names)] == sort_names
        after_sort = rest[len(sort_names) :] if sort_ok else rest
        ranges_ok = all(r in after_sort for r in ranges)
        if eq_covered == len(eq_set) and sort_ok and ranges_ok:
            best["full"] = idx["name"]
            break
        if eq_covered > best["partial_prefix_len"]:
            best["partial"], best["partial_prefix_len"] = idx["name"], eq_covered
    return best


def _confidence(plan: dict | None, frequency: int | None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    score = 0
    if plan and plan.get("collection_scan_detected"):
        score += 2
        reasons.append("COLLSCAN observed in the explain plan")
    eff = (plan or {}).get("efficiency_ratio")
    if eff is not None and eff >= 10:
        score += 1
        reasons.append(f"{eff:.1f} documents examined per document returned")
    if eff is not None and eff >= 1000:
        score += 1
    if frequency is not None and frequency >= 5:
        score += 1
        reasons.append(f"pattern observed {frequency} times")
    if plan and not plan.get("has_execution_stats"):
        score = max(score - 1, 0)
        reasons.append("no executionStats available (lower confidence)")
    level = "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW"
    return level, reasons


def recommend_index(
    *,
    collection: str,
    filter_: dict | None,
    sort: dict | list | None,
    existing_indexes: list[dict],
    plan: dict | None = None,
    frequency: int | None = None,
    cardinality: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Produce one recommendation record (possibly ``action: "none"``)."""
    preds = classify_predicates(filter_)
    sort_f = _sort_fields(sort)
    keys = build_index_keys(preds["equality"], sort_f, preds["range"], cardinality)
    base: dict[str, Any] = {
        "collection": collection,
        "status": "unverified_hypothesis",
        "predicates": preds,
        "existing_indexes": [{"name": i["name"], "key": dict(i["key"])} for i in existing_indexes],
        "notes": list(preds["notes"]),
    }
    if not keys:
        return {
            **base,
            "action": "none",
            "reason": "No indexable predicate or sort fields were found in this query.",
        }

    support = _existing_support(existing_indexes, keys, preds["equality"], sort_f, preds["range"])
    confidence, conf_reasons = _confidence(plan, frequency)
    problem = []
    if plan:
        if plan.get("collection_scan_detected"):
            problem.append("Query performs a collection scan")
        if plan.get("efficiency_ratio") is not None:
            problem.append(f"examined/returned ratio {plan['efficiency_ratio']:.1f}")

    if support["full"]:
        why = f"Existing index '{support['full']}' already matches the ESR layout for this query."
        if plan and plan.get("collection_scan_detected"):
            why += (
                " The planner nevertheless chose a COLLSCAN; likely causes: low selectivity, "
                "a small collection, or stale plan-cache decisions. Compare with hint() before "
                "adding anything."
            )
        return {**base, "action": "none", "reason": why, "supporting_evidence": conf_reasons}

    key_doc = dict(keys)
    spec = ", ".join(f"{f}: {d}" for f, d in keys)
    redundant_after = [
        i["name"]
        for i in existing_indexes
        if i["name"] != "_id_"
        and not i.get("unique")
        and list(i["key"].items()) == keys[: len(i["key"])]
        and len(i["key"]) < len(keys)
    ]
    est = (
        "ESTIMATE (not measured): documents examined could approach the number of documents "
        "returned if the index is selective; actual gain depends on data distribution."
    )
    return {
        **base,
        "action": "create_index",
        "problem": "; ".join(problem)
        or "No execution plan was supplied; recommendation is based on the query shape only",
        "suggested_index": {
            "keys": [[f, d] for f, d in keys],
            "key_doc": key_doc,
            "command": f"db.{collection}.createIndex({{ {spec} }})",
        },
        "rationale": {
            "equality_fields": [f for f, _ in keys if f in preds["equality"]],
            "sort_fields": [f for f, _ in sort_f],
            "range_fields": [f for f, _ in keys if f in preds["range"]],
            "principle": "Equality fields first, then sort fields, then range fields (ESR).",
            "extends_existing_index": support["partial"],
        },
        "supporting_evidence": conf_reasons,
        "expected_benefit": est,
        "costs": [
            "Every insert/update/delete touching the indexed fields pays extra write cost.",
            "Additional storage and RAM working-set pressure proportional to document count.",
            *(
                [
                    f"Existing index(es) {redundant_after} become prefix-redundant and could be "
                    "reviewed for removal after validation."
                ]
                if redundant_after
                else []
            ),
        ],
        "validation_steps": [
            "Run the baseline benchmark for this exact query before changing anything.",
            "Create the index on a staging copy (or explicitly confirm in DB Pulse).",
            "Re-run explain(executionStats): confirm IXSCAN replaces COLLSCAN and examined drops.",
            "Re-run the same benchmark and compare P95/P99, throughput and documents examined.",
            "Keep the index only if the measured result justifies its write/storage cost.",
        ],
        "confidence": confidence,
    }
