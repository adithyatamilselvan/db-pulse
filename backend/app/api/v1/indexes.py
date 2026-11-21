from __future__ import annotations

from fastapi import APIRouter

from app.analysis.recommendation_engine import classify_predicates, recommend_index
from app.core.dependencies import DB, AdminUser, CurrentUser, load_instance
from app.schemas.common import IndexCreateIn, IndexRollbackIn, QuerySpecIn
from app.services import connection_service as conn
from app.services import index_analyzer
from app.utils.query_validation import validate_query_spec
from app.utils.serialization import to_jsonable

router = APIRouter(prefix="/indexes", tags=["indexes"])


@router.get("/{instance_id}/{db_name}/{collection}")
def investigate(instance_id: str, db_name: str, collection: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    return to_jsonable(
        index_analyzer.investigate_indexes(conn.get_monitored_client(inst), db_name, collection)
    )


@router.post("/{instance_id}/recommend")
def recommend(instance_id: str, body: QuerySpecIn, user: CurrentUser, db: DB) -> dict:
    """Recommendation from the query shape + existing indexes (no explain executed)."""
    inst = load_instance(db, instance_id, user)
    spec = validate_query_spec(body.model_dump())
    client = conn.get_monitored_client(inst)
    existing = index_analyzer.list_indexes(client, spec["database"], spec["collection"])
    preds = classify_predicates(spec.get("filter"))
    card = index_analyzer.estimate_cardinality(
        client, spec["database"], spec["collection"], preds["equality"]
    )
    rec = recommend_index(
        collection=spec["collection"],
        filter_=spec.get("filter"),
        sort=spec.get("sort"),
        existing_indexes=existing,
        cardinality=card or None,
    )
    return {"recommendation": to_jsonable(rec)}


@router.post("/{instance_id}/create", status_code=201)
def create(instance_id: str, body: IndexCreateIn, user: AdminUser, db: DB) -> dict:
    """Create an index. Admin only; requires instance opt-in and explicit confirmation flags."""
    inst = load_instance(db, instance_id, user)
    change = index_analyzer.create_index_confirmed(
        conn.get_monitored_client(inst),
        db,
        instance=inst,
        user_id=user["_id"],
        db_name=body.database,
        coll=body.collection,
        keys=list(body.keys),
        name=body.name,
        confirmed=body.confirm,
        acknowledged_write_cost=body.acknowledge_write_cost,
    )
    return {"change": to_jsonable(change)}


@router.post("/{instance_id}/rollback")
def rollback(instance_id: str, body: IndexRollbackIn, user: AdminUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    change = index_analyzer.drop_index_created_by_dbpulse(
        conn.get_monitored_client(inst),
        db,
        instance=inst,
        user_id=user["_id"],
        change_id=body.change_id,
        confirmed=body.confirm,
    )
    return {"change": to_jsonable(change)}


@router.get("/{instance_id}/changes")
def changes(instance_id: str, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, instance_id, user)
    rows = db.index_changes.find({"instance_id": inst["_id"]}).sort("created_at", -1).limit(50)
    return {"items": to_jsonable(list(rows))}
