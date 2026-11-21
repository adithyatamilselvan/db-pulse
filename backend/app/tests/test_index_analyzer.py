from __future__ import annotations

from app.analysis.index_analyzer import analyze_indexes, find_duplicates, find_redundant_prefixes

ID = {"name": "_id_", "key": {"_id": 1}}


def i(name, unique=False, **key):
    d = {"name": name, "key": key}
    if unique:
        d["unique"] = True
    return d


def test_duplicate_detection():
    out = find_duplicates([ID, i("a_1", a=1), i("a_dup", a=1)])
    assert len(out) == 1 and out[0]["indexes"] == ["a_1", "a_dup"]


def test_same_keys_different_options_are_not_duplicates():
    assert find_duplicates([i("a_1", a=1), i("a_u", unique=True, a=1)]) == []


def test_redundant_prefix_detection_and_unique_exemption():
    out = find_redundant_prefixes([ID, i("a_1", a=1), i("a_1_b_1", a=1, b=1)])
    assert [x["indexes"] for x in out] == [["a_1", "a_1_b_1"]]
    assert find_redundant_prefixes([i("a_u", unique=True, a=1), i("a_1_b_1", a=1, b=1)]) == []
    assert find_redundant_prefixes([ID, i("a_1_b_1", a=1, b=1)]) == []


def test_unused_requires_stats_and_carries_caveat():
    idx = [ID, i("a_1", a=1), i("b_u", unique=True, b=1)]
    stats = [
        {"name": "a_1", "accesses": {"ops": 0, "since": "t"}},
        {"name": "b_u", "accesses": {"ops": 0, "since": "t"}},
        {"name": "_id_", "accesses": {"ops": 0, "since": "t"}},
    ]
    res = analyze_indexes(idx, stats)
    unused = [x for x in res["issues"] if x["type"] == "potentially_unused"]
    assert [x["indexes"] for x in unused] == [["a_1"]]  # unique and _id_ are exempt
    assert "does not prove" in unused[0]["message"]


def test_missing_index_stats_are_reported_as_unavailable_not_zero():
    res = analyze_indexes([ID, i("a_1", a=1)], None)
    assert res["index_stats_available"] is False
    assert all(row["accesses"] is None for row in res["indexes"])
    assert not any(x["type"] == "potentially_unused" for x in res["issues"])
