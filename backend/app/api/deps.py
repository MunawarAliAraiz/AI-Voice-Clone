"""
AI Voice Clone Studio — FastAPI dependencies and the API-key middleware.

The API layer depends on `SchedulerProtocol`, never on the concrete scheduler,
so the whole HTTP surface is tested against `FakeScheduler` with no GPU.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from ..config import Settings
from ..exceptions import PROBLEM_BASE_URI, PROBLEM_CONTENT_TYPE
from ..inference.catalog import CATALOG, ModelCatalog
from ..inference.protocol import SchedulerProtocol

__all__ = ["get_settings", "get_scheduler", "get_catalog", "ApiKeyMiddleware"]


def get_settings(request: Request) -> Settings:
    """The settings this app was built with — injected in tests, so a request
    reads the app's settings rather than a process-wide cached singleton."""
    return request.app.state.settings


def get_scheduler(request: Request) -> SchedulerProtocol:
    """The live scheduler, stashed on app state at startup (real or fake)."""
    return request.app.state.scheduler


def get_catalog() -> ModelCatalog:
    return CATALOG


#: Paths reachable without the API key. Media authenticates with its own signed
#: token (an `<audio>` tag cannot send `X-API-Key`); health and docs are open.
_EXEMPT_EXACT = frozenset({"/", "/api/health", "/health", "/openapi.json", "/docs", "/redoc"})
_EXEMPT_PREFIXES = ("/api/media/", "/docs", "/redoc")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Rejects requests without a valid `X-API-Key` when a key is configured.

    Added FIRST in `create_app` so it is the INNERMOST middleware; CORS is added
    LAST so it is outermost and answers preflight `OPTIONS` before this ever runs
    — the middleware-order trap that 403'd every cross-origin preflight in the
    predecessor. Empty key = open (single-user local dev).
    """

    def __init__(self, app: object, *, api_key: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._api_key = api_key

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self._api_key or request.method == "OPTIONS" or self._exempt(request.url.path):
            return await call_next(request)
        if request.headers.get("X-API-Key") != self._api_key:
            return JSONResponse(
                status_code=401,
                media_type=PROBLEM_CONTENT_TYPE,
                content={
                    "type": f"{PROBLEM_BASE_URI}/authentication-error",
                    "title": "Authentication required",
                    "status": 401,
                    "detail": "A valid X-API-Key header is required.",
                    "code": "AUTHENTICATION_ERROR",
                    "instance": request.url.path,
                },
            )
        return await call_next(request)

    @staticmethod
    def _exempt(path: str) -> bool:
        return path in _EXEMPT_EXACT or path.startswith(_EXEMPT_PREFIXES)
