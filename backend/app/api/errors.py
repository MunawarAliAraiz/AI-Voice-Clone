"""
AI Voice Clone Studio — Exception handlers (RFC 9457).

One place converts everything the app raises into `application/problem+json`.
Anything that is NOT an `AppError` reaching here is a bug: it is logged with a
traceback and returned as a bare 500 with no detail, because an internal message
(a torch stack trace, a file path) must never reach a client.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..exceptions import PROBLEM_BASE_URI, PROBLEM_CONTENT_TYPE, AppError

logger = logging.getLogger("app.api")

__all__ = ["install_exception_handlers"]


def _problem(status: int, code: str, title: str, detail: str, **extra: object) -> JSONResponse:
    body = {
        "type": f"{PROBLEM_BASE_URI}/{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        **extra,
    }
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CONTENT_TYPE)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_problem(instance=request.url.path),
            media_type=PROBLEM_CONTENT_TYPE,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own 422, reshaped as problem+json so clients see ONE error
        # format everywhere rather than two.
        return _problem(
            422, "VALIDATION_ERROR", "Invalid request",
            "The request body failed validation.",
            errors=jsonable_encoder(exc.errors()),
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _problem(
            500, "INTERNAL_ERROR", "Internal error",
            "An unexpected error occurred.",
            instance=request.url.path,
        )
