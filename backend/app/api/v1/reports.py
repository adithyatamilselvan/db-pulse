from __future__ import annotations

from datetime import datetime
from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.core.dependencies import DB, CurrentUser, load_instance
from app.core.exceptions import NotFoundError
from app.schemas.common import ReportIn
from app.services import monitoring_service, report_service
from app.utils.serialization import to_jsonable, utcnow

router = APIRouter(prefix="/reports", tags=["reports"])


def _parse(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


@router.post("", status_code=201)
def generate(body: ReportIn, user: CurrentUser, db: DB) -> dict:
    inst = load_instance(db, body.instance_id, user)
    start, end = monitoring_service.resolve_period(body.period, _parse(body.start), _parse(body.end))
    content = report_service.build_report(
        db,
        inst,
        start=start,
        end=end,
        comparison_id=body.comparison_id,
        title=body.title,
        include_synthetic=body.include_synthetic,
    )
    doc = {
        "owner_id": user["_id"],
        "instance_id": inst["_id"],
        "title": content["title"],
        "period": {"start": start, "end": end},
        "created_at": utcnow(),
        "includes_synthetic_data": content["includes_synthetic_data"],
        "content": content,
    }
    doc["_id"] = db.reports.insert_one(doc).inserted_id
    return {"report": to_jsonable({k: v for k, v in doc.items() if k != "content"})}


@router.get("")
def list_reports(user: CurrentUser, db: DB, limit: int = Query(30, ge=1, le=100)) -> dict:
    rows = db.reports.find({"owner_id": user["_id"]}, {"content": 0}).sort("created_at", -1).limit(limit)
    return {"items": to_jsonable(list(rows))}


def _load(db: DB, report_id: str, user: dict) -> dict:
    try:
        doc = db.reports.find_one({"_id": ObjectId(report_id), "owner_id": user["_id"]})
    except Exception as exc:
        raise NotFoundError("Report not found") from exc
    if not doc:
        raise NotFoundError("Report not found")
    return doc


@router.get("/{report_id}")
def get_report(report_id: str, user: CurrentUser, db: DB) -> dict:
    return {"report": to_jsonable(_load(db, report_id, user))}


@router.get("/{report_id}/download")
def download(
    report_id: str, user: CurrentUser, db: DB, format: Literal["pdf", "csv", "json"] = "pdf"
) -> Response:
    doc = _load(db, report_id, user)
    content = doc["content"]
    name = f"dbpulse-report-{report_id[-6:]}"
    if format == "json":
        import json

        return Response(
            json.dumps(content, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
        )
    if format == "csv":
        return Response(
            report_service.to_csv(content),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    return Response(
        report_service.to_pdf(content),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'},
    )
