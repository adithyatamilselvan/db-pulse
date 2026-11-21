"""Convert BSON-rich documents to JSON-safe structures."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import Decimal128, ObjectId


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return as_aware(value).isoformat()
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = "id" if k == "_id" else str(k)
            out[key] = to_jsonable(v)
        return out
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value
