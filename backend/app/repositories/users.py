"""Persistence helpers for users."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import ConflictError
from app.core.security import hash_password
from app.utils.serialization import to_jsonable, utcnow


def create_user(db: Database, *, email: str, password: str, name: str) -> dict[str, Any]:
    """First registered user becomes administrator; everyone after is a standard user."""
    role = "admin" if db.users.count_documents({}, limit=1) == 0 else "user"
    doc = {
        "email": email.lower(),
        "name": name,
        "password_hash": hash_password(password),
        "role": role,
        "active": True,
        "created_at": utcnow(),
    }
    try:
        doc["_id"] = db.users.insert_one(doc).inserted_id
    except DuplicateKeyError as exc:
        raise ConflictError("An account with this email already exists") from exc
    return doc


def public(user: dict[str, Any]) -> dict[str, Any]:
    return to_jsonable({k: user[k] for k in ("_id", "email", "name", "role", "created_at")})
