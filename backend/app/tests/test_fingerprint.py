from __future__ import annotations

from app.analysis.query_fingerprint import fingerprint, fingerprint_from_profile, query_shape


def fp(**kw):
    return fingerprint(operation="find", collection="orders", **kw)


def test_same_shape_different_values_group_together():
    assert fp(filter_={"customer_id": 1001, "status": "COMPLETED"}) == fp(
        filter_={"customer_id": 7, "status": "PENDING"}
    )


def test_field_order_does_not_matter_but_fields_do():
    assert fp(filter_={"a": 1, "b": 2}) == fp(filter_={"b": 9, "a": 3})
    assert fp(filter_={"a": 1, "b": 2}) != fp(filter_={"a": 1, "c": 2})


def test_operators_are_part_of_shape():
    assert fp(filter_={"a": {"$gt": 1}}) != fp(filter_={"a": 1})
    assert fp(filter_={"a": {"$gt": 1}}) != fp(filter_={"a": {"$lt": 1}})


def test_in_list_length_is_ignored():
    assert fp(filter_={"a": {"$in": [1, 2, 3]}}) == fp(filter_={"a": {"$in": [9]}})


def test_logical_branches_are_order_insensitive():
    assert fp(filter_={"$or": [{"a": 1}, {"b": 2}]}) == fp(filter_={"$or": [{"b": 5}, {"a": 6}]})


def test_sort_direction_and_collection_matter():
    assert fp(filter_={"a": 1}, sort={"a": 1}) != fp(filter_={"a": 1}, sort={"a": -1})
    assert fingerprint(operation="find", collection="x", filter_={"a": 1}) != fingerprint(
        operation="find", collection="y", filter_={"a": 1}
    )


def test_shape_never_contains_values():
    shape = query_shape(operation="find", collection="c", filter_={"email": "secret@x.com", "n": 5})
    assert "secret@x.com" not in str(shape) and "5" not in str(shape["filter"])


def test_profile_entry_fingerprint_matches_direct_fingerprint():
    entry = {
        "op": "query",
        "ns": "shop.orders",
        "command": {"find": "orders", "filter": {"customer_id": 5, "status": "A"}},
    }
    f1, _ = fingerprint_from_profile(entry)
    assert f1 == fp(filter_={"customer_id": 1, "status": "B"})


def test_aggregate_pipeline_values_ignored():
    p1 = [{"$match": {"a": 1}}, {"$group": {"_id": "$b", "n": {"$sum": 1}}}]
    p2 = [{"$match": {"a": 99}}, {"$group": {"_id": "$b", "n": {"$sum": 1}}}]
    assert fingerprint(operation="aggregate", collection="c", pipeline=p1) == fingerprint(
        operation="aggregate", collection="c", pipeline=p2
    )
