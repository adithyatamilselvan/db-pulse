"""In-memory sliding-window rate limiter (per client IP + route class).

Sufficient for a single-process deployment; behind several workers use a shared store (Redis).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings

AUTH_PATHS = ("/api/v1/auth/login", "/api/v1/auth/register")


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float = 60.0) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(window_s - (now - q[0])))
            q.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindow()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        s = get_settings()
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            ip = request.client.host if request.client else "unknown"
            is_auth = request.url.path in AUTH_PATHS
            key = f"{'auth' if is_auth else 'api'}:{ip}"
            limit = s.auth_rate_limit_per_minute if is_auth else s.rate_limit_per_minute
            ok, retry = limiter.allow(key, limit)
            if not ok:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                    content={
                        "error": {
                            "code": "rate_limited",
                            "message": "Too many requests; slow down",
                            "details": {"retry_after_s": retry},
                        }
                    },
                )
        return await call_next(request)
