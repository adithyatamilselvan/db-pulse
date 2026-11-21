from __future__ import annotations

import pytest

from app.core.exceptions import ValidationFailed
from app.utils.query_validation import validate_query_spec
from app.utils.redaction import mask_uri, redact_values, scrub_text


def base(**kw):
    return {"database": "shop", "collection": "orders", **kw}


def test_mask_uri_hides_credentials():
    assert "hunter2" not in mask_uri("mongodb://admin:hunter2@host:27017/db")
    assert "hunter2" not in scrub_text("failed for mongodb+srv://u:hunter2@c.mongodb.net/x ok")
    assert mask_uri("mongodb://localhost:27017") == "mongodb://localhost:27017"


def test_redact_values_keeps_shape_only():
    out = redact_values({"email": "a@b.c", "n": {"$gt": 5}, "tags": {"$in": ["x", "y"]}, "password": "p"})
    assert out == {"email": "?", "n": {"$gt": "?"}, "tags": {"$in": ["?"]}, "password": "***"}


@pytest.mark.parametrize(
    "bad",
    [
        {"filter": {"$where": "sleep(1000)"}},
        {"filter": {"a": {"$function": {"body": "x", "args": [], "lang": "js"}}}},
        {"operation": "aggregate", "pipeline": [{"$out": "x"}]},
        {"operation": "aggregate", "pipeline": [{"$merge": {"into": "x"}}]},
        {"operation": "aggregate", "pipeline": [{"$lookup": {"from": "a"}}]},
        {"operation": "aggregate", "pipeline": []},
        {"operation": "delete"},
        {"sort": {"a": 2}},
        {"limit": 0},
        {"filter": "not json"},
        {"filter": "[1,2]"},
    ],
)
def test_unsafe_or_malformed_specs_are_rejected(bad):
    with pytest.raises(ValidationFailed):
        validate_query_spec(base(**bad))


@pytest.mark.parametrize("db", ["admin", "local", "config"])
def test_system_databases_blocked(db):
    with pytest.raises(ValidationFailed):
        validate_query_spec({"database": db, "collection": "x"})


def test_system_collection_blocked():
    with pytest.raises(ValidationFailed):
        validate_query_spec(base(collection="system.profile"))


def test_depth_and_size_limits():
    deep: dict = {"a": 1}
    for _ in range(20):
        deep = {"a": deep}
    with pytest.raises(ValidationFailed):
        validate_query_spec(base(filter=deep))
    with pytest.raises(ValidationFailed):
        validate_query_spec(base(filter={"a": "x" * 25000}))


def test_valid_spec_normalises_json_strings_and_extended_json():
    s = validate_query_spec(
        base(
            filter='{"customer_id": 1001, "id": {"$oid": "507f1f77bcf86cd799439011"}}',
            sort='{"a": -1}',
            limit=5,
        )
    )
    assert s["filter"]["customer_id"] == 1001 and s["sort"] == {"a": -1} and s["limit"] == 5
    assert type(s["filter"]["id"]).__name__ == "ObjectId"
