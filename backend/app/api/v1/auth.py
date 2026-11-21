from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DB, CurrentUser
from app.core.exceptions import UnauthorizedError, ValidationFailed
from app.core.security import create_access_token, hash_password, verify_password
from app.repositories import users as users_repo
from app.schemas.common import LoginIn, ProfileUpdate, RegisterIn

router = APIRouter(prefix="/auth", tags=["auth"])

# Verifying against a dummy hash when the user does not exist keeps response time uniform,
# so login does not reveal which emails are registered.
_DUMMY_HASH = hash_password("dummy-password-1234")


@router.post("/register", status_code=201)
def register(body: RegisterIn, db: DB) -> dict:
    user = users_repo.create_user(db, email=body.email, password=body.password, name=body.name)
    return {
        "user": users_repo.public(user),
        "access_token": create_access_token(str(user["_id"]), user["role"]),
        "token_type": "bearer",
    }


@router.post("/login")
def login(body: LoginIn, db: DB) -> dict:
    user = db.users.find_one({"email": body.email.lower()})
    ok = verify_password(body.password, user["password_hash"] if user else _DUMMY_HASH)
    if not user or not ok or not user.get("active", True):
        raise UnauthorizedError("Invalid email or password")
    return {
        "user": users_repo.public(user),
        "access_token": create_access_token(str(user["_id"]), user["role"]),
        "token_type": "bearer",
    }


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {"user": users_repo.public(user)}


@router.patch("/me")
def update_me(body: ProfileUpdate, user: CurrentUser, db: DB) -> dict:
    update: dict = {}
    if body.name:
        update["name"] = body.name
    if body.new_password:
        if not body.current_password or not verify_password(body.current_password, user["password_hash"]):
            raise ValidationFailed("Current password is incorrect")
        update["password_hash"] = hash_password(body.new_password)
    if update:
        db.users.update_one({"_id": user["_id"]}, {"$set": update})
        user.update(update)
    return {"user": users_repo.public(user)}


@router.post("/logout")
def logout(_: CurrentUser) -> dict:
    """Tokens are stateless and short-lived; the client discards its token on logout."""
    return {"ok": True}
