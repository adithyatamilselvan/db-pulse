"""Redaction helpers: keep query *shape*, drop query *values*; mask connection strings."""

from __future__ import annotations

import re
from typing import Any

_URI_CRED = re.compile(r"(mongodb(?:\+srv)?://)([^@/\s]+)@", re.IGNORECASE)
_SECRET_KEYS = {"password", "pwd", "secret", "token", "api_key", "apikey", "authorization"}


def mask_uri(uri: str) -> str:
    """Return the URI with credentials replaced, safe for logs and API responses."""
    return _URI_CRED.sub(r"\1***:***@", uri)


def scrub_text(text: str) -> str:
    """Remove anything resembling a MongoDB URI credential from free text."""
    return _URI_CRED.sub(r"\1***:***@", text)


def redact_values(node: Any) -> Any:
    """Replace every leaf value with '?', preserving keys, operators and structure.

    ``{"customer_id": 1001, "status": {"$in": ["A", "B"]}}`` becomes
    ``{"customer_id": "?", "status": {"$in": ["?"]}}``.
    """
    if isinstance(node, dict):
        return {
            k: ("***" if isinstance(k, str) and k.lower() in _SECRET_KEYS else redact_values(v))
            for k, v in node.items()
        }
    if isinstance(node, (list, tuple)):
        if not node:
            return []
        return (
            [redact_values(node[0])]
            if all(not isinstance(x, (dict, list)) for x in node)
            else [redact_values(x) for x in node]
        )
    return "?"
