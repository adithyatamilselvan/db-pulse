"""FastAPI dependencies: internal DB, authentication, authorization, instance access."""

from __future__ import annotations

from typing import Annotated, Any

from bson import ObjectId
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.database import Database

from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import decode_access_token
from app.database import get_internal_db

bearer = HTTPBearer(auto_error=False)


def get_db() -> Database:
    return get_internal_db()


DB = Annotated[Database, Depends(get_db)]


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], db: DB
) -> dict[str, Any]:
    if creds is None or creds.scheme.lower() != "bearer":
        raise UnauthorizedError("Authentication required")
    payload = decode_access_token(creds.credentials)
    try:
        user = db.users.find_one({"_id": ObjectId(payload["sub"])})
    except Exception as exc:
        raise UnauthorizedError("Invalid access token") from exc
    if not user or not user.get("active", True):
        raise UnauthorizedError("User no longer exists or is disabled")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


def require_admin(user: CurrentUser) -> dict[str, Any]:
    if user.get("role") != "admin":
        raise ForbiddenError("Administrator role required")
    return user


AdminUser = Annotated[dict[str, Any], Depends(require_admin)]


def load_instance(db: Database, instance_id: str, user: dict) -> dict[str, Any]:
    """Return an instance the user may access. Others' instances look like 'not found'."""
    try:
        oid = ObjectId(instance_id)
    except Exception as exc:
        raise NotFoundError("Database instance not found") from exc
    inst = db.instances.find_one({"_id": oid})
    if not inst or (inst["owner_id"] != user["_id"] and user.get("role") != "admin"):
        raise NotFoundError("Database instance not found")
    return inst


def accessible_instance_ids(db: Database, user: dict) -> list[ObjectId]:
    q = {} if user.get("role") == "admin" else {"owner_id": user["_id"]}
    return [i["_id"] for i in db.instances.find(q, {"_id": 1})]
