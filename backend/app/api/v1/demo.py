from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from app.config import get_settings
from app.core.dependencies import DB, AdminUser, load_instance
from app.core.exceptions import ValidationFailed
from app.schemas.common import Strict
from app.services import connection_service as conn
from app.services import demo_service

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoSetupIn(Strict):
    orders: int = Field(default=200_000, ge=1000, le=2_000_000)
    seed: int = 42
    confirm: bool = False


class DemoResetIn(Strict):
    instance_id: str
    drop_demo_database: bool = False
    confirm: bool = False


@router.post("/setup", status_code=201)
def setup(body: DemoSetupIn, user: AdminUser, db: DB) -> dict:
    """Create the demo instance, REAL demo data in the demo monitored DB, and SYNTHETIC history."""
    if not body.confirm:
        raise ValidationFailed(
            "Demo setup writes a demo dataset to the configured demo MongoDB "
            "(skipped if it is already there; nothing is dropped). Set confirm=true."
        )
    uri = get_settings().demo_mongo_uri
    if not uri:
        raise ValidationFailed("DEMO_MONGO_URI is not configured")
    test = conn.test_connection(uri)
    if not test["ok"]:
        raise ValidationFailed(f"Demo MongoDB is not reachable: {test['error']}")
    return demo_service.setup_demo(
        db,
        user=user,
        demo_uri=uri,
        orders=body.orders,
        seed=body.seed,
        client_factory=conn.get_monitored_client,
        version=test["version"],
    )


@router.post("/reset")
def reset(body: DemoResetIn, user: AdminUser, db: DB) -> dict:
    if not body.confirm:
        raise ValidationFailed("Reset requires confirm=true")
    inst = load_instance(db, body.instance_id, user)
    if not inst.get("is_demo"):
        raise ValidationFailed("Only the demo instance can be reset here")
    removed = demo_service.reset_synthetic(db, inst["_id"])
    dropped = None
    if body.drop_demo_database:
        conn.get_monitored_client(inst).drop_database(demo_service.demo_db_name())
        dropped = demo_service.demo_db_name()
    return {"removed_synthetic": removed, "dropped_database": dropped}
