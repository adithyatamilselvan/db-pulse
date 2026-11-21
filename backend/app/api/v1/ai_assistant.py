from __future__ import annotations

from bson import ObjectId
from fastapi import APIRouter, Query

from app.core.dependencies import DB, CurrentUser, load_instance
from app.core.exceptions import NotFoundError
from app.schemas.common import AIInvestigateIn
from app.services import ai_service
from app.utils.serialization import to_jsonable

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def status(_: CurrentUser) -> dict:
    """Which engine will answer: Cohere (if configured) or the local rule-based engine."""
    return ai_service.provider_status()


@router.post("/investigate")
def investigate(body: AIInvestigateIn, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, body.instance_id, user)
    answer = ai_service.investigate(
        db,
        inst,
        body.question,
        fingerprint=body.fingerprint,
        source=body.source,
        plan_id=body.plan_id,
        comparison_id=body.comparison_id,
    )
    convo = ai_service.save_turn(
        db,
        inst,
        user["_id"],
        body.question,
        answer,
        conversation_id=body.conversation_id,
        refs_hint={
            "fingerprint": body.fingerprint,
            "plan_id": body.plan_id,
            "comparison_id": body.comparison_id,
        },
    )
    return {"conversation_id": str(convo["_id"]), "answer": to_jsonable(answer)}


@router.get("/conversations")
def conversations(user: CurrentUser, db: DB, limit: int = Query(30, ge=1, le=100)) -> dict:
    q = {"owner_id": user["_id"]}
    rows = db.investigations.find(q, {"messages": 0}).sort("updated_at", -1).limit(limit)
    return {"items": to_jsonable(list(rows))}


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: str, user: CurrentUser, db: DB) -> dict:
    try:
        doc = db.investigations.find_one({"_id": ObjectId(conversation_id), "owner_id": user["_id"]})
    except Exception as exc:
        raise NotFoundError("Conversation not found") from exc
    if not doc:
        raise NotFoundError("Conversation not found")
    return {"conversation": to_jsonable(doc)}
