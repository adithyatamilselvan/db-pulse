"""DB Pulse API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1 import (
    ai_assistant,
    alerts,
    auth,
    benchmarks,
    databases,
    demo,
    execution_plans,
    health,
    indexes,
    monitoring,
    queries,
    reports,
)
from app.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.ratelimit import RateLimitMiddleware
from app.database import ensure_indexes
from app.services import connection_service
from app.utils.redaction import scrub_text


class _ScrubFilter(logging.Filter):
    """Defence in depth: never let a connection string with credentials reach the logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub_text(str(record.msg))
        if record.args:
            record.args = tuple(
                scrub_text(a) if isinstance(a, str) else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
            resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return resp


def create_app() -> FastAPI:
    settings = get_settings()  # fails fast on missing/invalid configuration
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_ScrubFilter())

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        ensure_indexes()
        scheduler = None
        if settings.enable_inprocess_scheduler:
            from app.workers.scheduler import Scheduler

            scheduler = Scheduler()
            scheduler.start()
        yield
        if scheduler:
            scheduler.stop()
        connection_service.pool.close_all()

    app = FastAPI(
        title="DB Pulse API",
        version="1.0.0",
        lifespan=lifespan,
        description="MongoDB performance investigation platform. Evidence first.",
    )
    app.add_middleware(SecurityHeaders)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_exception_handlers(app)

    v1 = APIRouter(prefix="/api/v1")
    for module in (
        health,
        auth,
        databases,
        monitoring,
        queries,
        execution_plans,
        indexes,
        benchmarks,
        alerts,
        ai_assistant,
        reports,
        demo,
    ):
        v1.include_router(module.router)
    app.include_router(v1)
    return app


app = create_app()
