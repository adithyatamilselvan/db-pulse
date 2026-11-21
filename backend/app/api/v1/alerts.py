from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Query

from app.core.dependencies import DB, CurrentUser, accessible_instance_ids, load_instance
from app.core.exceptions import NotFoundError
from app.models.enums import ALERT_TYPES, SEVERITIES
from app.services import alert_service
from app.utils.serialization import to_jsonable

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _load(db: DB, alert_id: str, user: dict) -> dict:
    try:
        a = db.alerts.find_one({"_id": ObjectId(alert_id)})
    except Exception as exc:
        raise NotFoundError("Alert not found") from exc
    if not a:
        raise NotFoundError("Alert not found")
    load_instance(db, str(a["instance_id"]), user)
    return a


@router.get("")
def list_alerts(
    user: CurrentUser,
    db: DB,
    instance_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    include_synthetic: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    ids = [load_instance(db, instance_id, user)["_id"]] if instance_id else accessible_instance_ids(db, user)
    q: dict = {"instance_id": {"$in": ids}}
    if status:
        q["status"] = status
    if severity and severity.upper() in SEVERITIES:
        q["severity"] = severity.upper()
    if type and type in ALERT_TYPES:
        q["type"] = type
    if not include_synthetic:
        q["source"] = "observed"
    if start or end:
        q["created_at"] = {**({"$gte": start} if start else {}), **({"$lte": end} if end else {})}
    total = db.alerts.count_documents(q)
    rows = db.alerts.find(q).sort("created_at", -1).skip(offset).limit(limit)
    return {"total": total, "items": to_jsonable(list(rows))}


@router.get("/summary")
def summary(user: CurrentUser, db: DB) -> dict:
    ids = accessible_instance_ids(db, user)
    pipeline = [
        {"$match": {"instance_id": {"$in": ids}}},
        {"$group": {"_id": {"status": "$status", "severity": "$severity"}, "n": {"$sum": 1}}},
    ]
    out: dict = {"by_status": {}, "by_severity_active": {}}
    for r in db.alerts.aggregate(pipeline):
        st, sev = r["_id"]["status"], r["_id"]["severity"]
        out["by_status"][st] = out["by_status"].get(st, 0) + r["n"]
        if st != "resolved":
            out["by_severity_active"][sev] = out["by_severity_active"].get(sev, 0) + r["n"]
    return out


@router.get("/{alert_id}")
def get_alert(alert_id: str, user: CurrentUser, db: DB) -> dict:
    return {"alert": to_jsonable(_load(db, alert_id, user))}


@router.post("/{alert_id}/acknowledge")
def acknowledge(alert_id: str, user: CurrentUser, db: DB) -> dict:
    _load(db, alert_id, user)
    return {"alert": to_jsonable(alert_service.acknowledge(db, alert_id, user["_id"]))}


@router.post("/{alert_id}/resolve")
def resolve(alert_id: str, user: CurrentUser, db: DB) -> dict:
    _load(db, alert_id, user)
    return {"alert": to_jsonable(alert_service.resolve(db, alert_id, user["_id"]))}
