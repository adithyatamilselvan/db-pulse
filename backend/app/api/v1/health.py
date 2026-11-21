from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.dependencies import DB
from app.services.ai_service import provider_status

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: DB) -> JSONResponse:
    try:
        db.command("ping")
        return JSONResponse({"status": "ready", "internal_db": "ok", "ai": provider_status()["mode"]})
    except Exception:
        return JSONResponse({"status": "unavailable", "internal_db": "down"}, status_code=503)
