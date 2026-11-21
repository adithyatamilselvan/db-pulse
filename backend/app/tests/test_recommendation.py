from __future__ import annotations

from app.analysis.plan_parser import parse_explain
from app.analysis.recommendation_engine import classify_predicates, recommend_index
from app.tests.test_plan_parser import load

ID = {"name": "_id_", "key": {"_id": 1}}


def idx(name, **key):
    return {"name": name, "key": key}


def test_equality_predicates_become_compound_index():
    r = recommend_index(
        collection="orders",
        filter_={"customer_id": 1001, "status": "COMPLETED"},
        sort=None,
        existing_indexes=[ID],
        plan=parse_explain(load("find_collscan")),
    )
    assert r["action"] == "create_index" and r["status"] == "unverified_hypothesis"
    assert r["suggested_index"]["keys"] == [["customer_id", 1], ["status", 1]]
    assert r["suggested_index"]["command"] == "db.orders.createIndex({ customer_id: 1, status: 1 })"
    assert r["confidence"] in ("MEDIUM", "HIGH")
    assert r["supporting_evidence"] and r["validation_steps"] and r["costs"]
    assert "ESTIMATE" in r["expected_benefit"]


def test_esr_ordering_equality_then_sort_then_range():
    r = recommend_index(
        collection="o",
        filter_={"status": "A", "total": {"$gt": 5}, "cid": 1},
        sort={"created": -1},
        existing_indexes=[ID],
    )
    assert [k for k, _ in r["suggested_index"]["keys"]] == ["status", "cid", "created", "total"]
    assert dict(r["suggested_index"]["keys"])["created"] == -1


def test_cardinality_orders_equality_fields_most_selective_first():
    r = recommend_index(
        collection="o",
        filter_={"status": "A", "cid": 1},
        sort=None,
        existing_indexes=[ID],
        cardinality={"status": 3, "cid": 5000},
    )
    assert [k for k, _ in r["suggested_index"]["keys"]] == ["cid", "status"]


def test_no_recommendation_when_matching_index_exists():
    r = recommend_index(
        collection="o", filter_={"a": 1, "b": 2}, sort=None, existing_indexes=[ID, idx("a_1_b_1", a=1, b=1)]
    )
    assert r["action"] == "none" and "already matches" in r["reason"]


def test_matching_index_with_collscan_explains_mismatch_instead_of_recommending():
    r = recommend_index(
        collection="o",
        filter_={"a": 1},
        sort=None,
        existing_indexes=[ID, idx("a_1", a=1)],
        plan=parse_explain(load("find_collscan")),
    )
    assert r["action"] == "none" and "COLLSCAN" in r["reason"]


def test_partial_prefix_index_is_extended_and_flagged_redundant_afterwards():
    r = recommend_index(
        collection="o", filter_={"a": 1, "b": 2}, sort=None, existing_indexes=[ID, idx("a_1", a=1)]
    )
    assert r["action"] == "create_index"
    assert r["rationale"]["extends_existing_index"] == "a_1"
    assert any("a_1" in c for c in r["costs"])


def test_negation_and_or_are_not_blindly_indexed():
    preds = classify_predicates({"a": {"$ne": 1}, "$or": [{"b": 1}, {"c": 2}], "d": 4})
    assert preds["equality"] == ["d"] and any("$or" in n for n in preds["notes"])
    assert any("$ne" in n for n in preds["notes"])


def test_large_in_list_treated_as_range_small_as_equality():
    assert classify_predicates({"a": {"$in": [1, 2]}})["equality"] == ["a"]
    assert classify_predicates({"a": {"$in": list(range(500))}})["range"] == ["a"]


def test_no_indexable_fields_yields_no_action():
    r = recommend_index(collection="o", filter_={"$or": [{"a": 1}]}, sort=None, existing_indexes=[ID])
    assert r["action"] == "none"


def test_low_evidence_gives_low_confidence():
    r = recommend_index(collection="o", filter_={"a": 1}, sort=None, existing_indexes=[ID])
    assert r["confidence"] == "LOW"


def test_hashed_existing_indexes_are_ignored_for_coverage():
    r = recommend_index(
        collection="o",
        filter_={"a": 1},
        sort=None,
        existing_indexes=[ID, {"name": "a_hashed", "key": {"a": "hashed"}}],
    )
    assert r["action"] == "create_index"
