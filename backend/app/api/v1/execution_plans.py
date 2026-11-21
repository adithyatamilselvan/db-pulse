from __future__ import annotations

from bson import ObjectId
from fastapi import APIRouter, Query

from app.core.dependencies import DB, CurrentUser, accessible_instance_ids, load_instance
from app.core.exceptions import NotFoundError
from app.schemas.common import QuerySpecIn
from app.services import connection_service as conn
from app.services import investigation_service
from app.utils.query_validation import validate_query_spec
from app.utils.serialization import to_jsonable

router = APIRouter(prefix="/execution-plans", tags=["execution-plans"])


@router.post("/{instance_id}/explain", status_code=201)
def explain(instance_id: str, body: QuerySpecIn, user: CurrentUser, db: DB) -> dict:
    """Run a real explain('executionStats') and analyze it. Read-only on the monitored DB."""
    inst = load_instance(db, instance_id, user)
    spec = validate_query_spec(body.model_dump())
    doc = investigation_service.run_investigation(
        db, inst, conn.get_monitored_client(inst), spec, user_id=user["_id"]
    )
    return {"plan": investigation_service.present_plan(doc, include_raw=True)}


@router.get("")
def list_plans(
    user: CurrentUser,
    db: DB,
    instance_id: str | None = None,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    ids = [load_instance(db, instance_id, user)["_id"]] if instance_id else accessible_instance_ids(db, user)
    q = {"instance_id": {"$in": ids}}
    rows = list(
        db.execution_plans.find(
            q, {"raw_explain_enc": 0, "index_analysis": 0, "analysis.tree": 0, "analysis.stages": 0}
        )
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    )
    return {"total": db.execution_plans.count_documents(q), "items": to_jsonable(rows)}


@router.get("/{plan_id}")
def get_plan(plan_id: str, user: CurrentUser, db: DB) -> dict:
    try:
        doc = db.execution_plans.find_one({"_id": ObjectId(plan_id)})
    except Exception as exc:
        raise NotFoundError("Execution plan not found") from exc
    if not doc:
        raise NotFoundError("Execution plan not found")
    load_instance(db, str(doc["instance_id"]), user)  # authorization
    return {"plan": investigation_service.present_plan(doc, include_raw=True)}
