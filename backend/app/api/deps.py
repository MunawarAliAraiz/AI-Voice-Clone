"""
AI Voice Clone Studio — FastAPI dependencies and the API-key middleware.

The API layer depends on `SchedulerProtocol`, never on the concrete scheduler,
so the whole HTTP surface is tested against `FakeScheduler` with no GPU.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from ..config import Settings
from ..db import Database
from ..domain.urdu_text import effective_lexicon
from ..exceptions import PROBLEM_BASE_URI, PROBLEM_CONTENT_TYPE
from ..inference.catalog import CATALOG, ModelCatalog
from ..inference.protocol import SchedulerProtocol
from ..jobs import JobRunner

__all__ = [
    "get_settings",
    "get_scheduler",
    "get_catalog",
    "get_db",
    "get_job_runner",
    "get_lexicon",
    "ApiKeyMiddleware",
]


def get_settings(request: Request) -> Settings:
    """The settings this app was built with — injected in tests, so a request
    reads the app's settings rather than a process-wide cached singleton."""
    return request.app.state.settings


def get_db(request: Request) -> Database:
    """The live database connection, opened at startup (or injected in tests)."""
    return request.app.state.db


def get_scheduler(request: Request) -> SchedulerProtocol:
    """The live scheduler, stashed on app state at startup (real or fake)."""
    return request.app.state.scheduler


def get_catalog() -> ModelCatalog:
    return CATALOG


def get_job_runner(request: Request) -> JobRunner:
    """The live job runner, started at app startup (or injected in tests)."""
    return request.app.state.jobs


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
        provided = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(self._api_key, provided):
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


async def get_lexicon(db: Annotated[Database, Depends(get_db)]) -> dict[str, str]:
    """
    The user's pronunciation dictionary, merged over the shipped defaults.

    This is the I/O that `routing.resolve()` is forbidden to do itself (golden
    rule 4). Loading it here, in a dependency, means routing receives a plain
    mapping and stays a pure function of its arguments — testable with no
    database, and unable to route differently depending on what a query
    happened to return.

    Disabled rows are loaded deliberately: `effective_lexicon` needs them,
    because a disabled row whose key matches a shipped default is the only way
    to switch that default off.
    """
    rows = await db.list_pronunciations(language="ur")
    return effective_lexicon(rows)
