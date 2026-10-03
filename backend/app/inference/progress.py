"""Request-scoped progress shared by local queues and remote inference calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

ProgressCallback = Callable[[str, str | None], Awaitable[None]]
_callback: ContextVar[ProgressCallback | None] = ContextVar("inference_progress", default=None)
_request_id: ContextVar[str | None] = ContextVar("inference_request_id", default=None)


@contextmanager
def progress_scope(
    callback: ProgressCallback, request_id: str | None = None,
) -> Iterator[None]:
    """Bind progress for this request; nested/concurrent scopes remain isolated."""
    callback_token = _callback.set(callback)
    request_token = _request_id.set(request_id if request_id is not None else _request_id.get())
    try:
        yield
    finally:
        _request_id.reset(request_token)
        _callback.reset(callback_token)


def current_request_id() -> str | None:
    return _request_id.get()


async def emit_progress(stage: str, model_id: str | None = None) -> None:
    """Report an actual boundary; absence of a listener is a no-op."""
    callback = _callback.get()
    if callback is not None:
        await callback(stage, model_id)
