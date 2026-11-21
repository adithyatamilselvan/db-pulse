from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DB, CurrentUser, load_instance
from app.core.exceptions import ConnectionFailed, ValidationFailed
from app.repositories import instances as repo
from app.schemas.common import InstanceCreate, InstanceSettings, InstanceUpdate, UriTest
from app.services import connection_service as conn
from app.services import index_analyzer
from app.utils.serialization import to_jsonable, utcnow

router = APIRouter(prefix="/databases", tags=["databases"])


@router.post("/test")
def test_uri(body: UriTest, _: CurrentUser) -> dict:
    """Test a connection string without storing it. The URI is never echoed back."""
    return conn.test_connection(body.uri)


@router.post("", status_code=201)
def register(body: InstanceCreate, user: CurrentUser, db: DB) -> dict:
    result = conn.test_connection(body.uri)
    if not result["ok"]:
        raise ConnectionFailed(
            f"Connection test failed: {result['error']}", details={"error_code": result["error_code"]}
        )
    inst = repo.create_instance(
        db,
        owner_id=user["_id"],
        name=body.name,
        uri=body.uri,
        allow_modifications=body.allow_modifications,
        server_version=result["version"],
    )
    return {"instance": repo.public(inst), "connection": {"latency_ms": result["latency_ms"]}}


@router.get("")
def list_instances(user: CurrentUser, db: DB) -> dict:
    q = {} if user["role"] == "admin" else {"owner_id": user["_id"]}
    rows = db.instances.find(q).sort("created_at", 1)
    return {"instances": [repo.public(i) for i in rows]}


@router.get("/{instance_id}")
def get_instance(instance_id: str, user: CurrentUser, db: DB) -> dict:
    return {"instance": repo.public(load_instance(db, instance_id, user))}


@router.patch("/{instance_id}")
def update_instance(instance_id: str, body: InstanceUpdate, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    update: dict = {}
    if body.name is not None:
        update["name"] = body.name
    if body.allow_modifications is not None:
        update["allow_modifications"] = body.allow_modifications
    if body.uri is not None:
        result = conn.test_connection(body.uri)
        if not result["ok"]:
            raise ConnectionFailed(
                f"Connection test failed: {result['error']}", details={"error_code": result["error_code"]}
            )
        from app.core.encryption import encrypt_secret

        update.update(
            uri_enc=encrypt_secret(body.uri),
            host_display=repo.host_display(body.uri),
            server_version=result["version"],
            status="connected",
            last_error=None,
            checkpoints={},
        )
        conn.pool.evict(str(inst["_id"]))
    if update:
        from pymongo.errors import DuplicateKeyError

        try:
            db.instances.update_one({"_id": inst["_id"]}, {"$set": update})
        except DuplicateKeyError as exc:
            from app.core.exceptions import ConflictError

            raise ConflictError("You already have an instance with this name") from exc
    return {"instance": repo.public(db.instances.find_one({"_id": inst["_id"]}))}


@router.delete("/{instance_id}")
def delete_instance(instance_id: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    conn.pool.evict(str(inst["_id"]))
    counts = repo.delete_instance_data(db, inst["_id"])
    return {"deleted": True, "removed_records": counts}


@router.post("/{instance_id}/check")
def check(instance_id: str, user: CurrentUser, db: DB) -> dict:
    """Re-test the stored connection and update the status shown in the UI."""
    inst = load_instance(db, instance_id, user)
    from app.core.encryption import decrypt_secret

    result = conn.test_connection(decrypt_secret(inst["uri_enc"]))
    status = "connected" if result["ok"] else "error"
    if not inst.get("enabled", True):
        status = "disconnected"
    db.instances.update_one(
        {"_id": inst["_id"]},
        {
            "$set": {
                "status": status,
                "last_check_at": utcnow(),
                "last_error": result["error"],
                **({"server_version": result["version"]} if result["ok"] else {}),
            }
        },
    )
    if not result["ok"]:
        from app.services import alert_service

        alert_service.raise_alert(
            db,
            instance=inst,
            alert_type="CONNECTION_FAILURE",
            severity="HIGH",
            title=f"Cannot connect to {inst['name']}",
            message=result["error"] or "",
            evidence={"error_code": result["error_code"]},
        )
    return {"instance": repo.public(db.instances.find_one({"_id": inst["_id"]})), "connection": result}


@router.post("/{instance_id}/disconnect")
def disconnect(instance_id: str, user: CurrentUser, db: DB) -> dict:
    """Stop monitoring and close pooled connections; history is kept."""
    inst = load_instance(db, instance_id, user)
    conn.pool.evict(str(inst["_id"]))
    db.instances.update_one({"_id": inst["_id"]}, {"$set": {"enabled": False, "status": "disconnected"}})
    return {"instance": repo.public(db.instances.find_one({"_id": inst["_id"]}))}


@router.post("/{instance_id}/connect")
def reconnect(instance_id: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    db.instances.update_one({"_id": inst["_id"]}, {"$set": {"enabled": True}})
    return check(instance_id, user, db)


@router.get("/{instance_id}/settings")
def get_settings_(instance_id: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    return {"settings": inst.get("settings", {})}


@router.put("/{instance_id}/settings")
def put_settings(instance_id: str, body: InstanceSettings, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    from app.models.enums import ALERT_TYPES

    bad = [k for k in body.severity_overrides if k not in ALERT_TYPES]
    if bad:
        raise ValidationFailed(f"Unknown alert type(s) in severity_overrides: {bad}")
    db.instances.update_one({"_id": inst["_id"]}, {"$set": {"settings": body.model_dump()}})
    return {"settings": body.model_dump()}


# --- explorer ------------------------------------------------------------------------
@router.get("/{instance_id}/explorer")
def explorer_databases(instance_id: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    client = conn.get_monitored_client(inst)
    dbs = conn.list_databases(client)
    return {"databases": dbs}


@router.get("/{instance_id}/explorer/{db_name}/collections")
def explorer_collections(instance_id: str, db_name: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    client = conn.get_monitored_client(inst)
    return {"database": db_name, "collections": conn.list_collections(client, db_name)}


@router.get("/{instance_id}/explorer/{db_name}/{collection}/indexes")
def explorer_indexes(instance_id: str, db_name: str, collection: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    client = conn.get_monitored_client(inst)
    return to_jsonable(index_analyzer.investigate_indexes(client, db_name, collection))
