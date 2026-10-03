"""Bounded in-flight worker activity. Scripts, voices and paths are never stored."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException

from ..inference.progress import progress_scope

_STAGES = frozenset({"queued", "loading_model", "generating", "analyzing", "converting"})


def request_identity(value: str | None) -> str:
    if value is None:
        return uuid4().hex
    if len(value) not in {32, 36}:
        raise HTTPException(422, "X-Request-Id must be a UUID or 32 hexadecimal characters")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            422, "X-Request-Id must be a UUID or 32 hexadecimal characters",
        ) from exc
    return parsed.hex if len(value) == 32 else str(parsed)


class ActivityStore:
    def __init__(self, model_ids: frozenset[str], *, capacity: int = 128) -> None:
        self._model_ids = model_ids
        self._capacity = capacity
        self._operations: dict[str, dict] = {}
        self.worker_instance_id = uuid4().hex

    @contextmanager
    def track(self, request_id: str, model_id: str) -> Iterator[None]:
        if request_id in self._operations:
            raise HTTPException(409, "This request is already active")
        if len(self._operations) >= self._capacity:
            raise HTTPException(503, "The worker has too many active requests. Try again shortly")
        if model_id not in self._model_ids:
            raise HTTPException(404, "Unknown model ID")
        record = {
            "request_id": request_id, "model_id": model_id, "stage": "queued",
            "updated_at": datetime.now(UTC).isoformat(), "started": time.monotonic(),
        }
        self._operations[request_id] = record

        async def update(stage: str, selected_model: str | None) -> None:
            if stage not in _STAGES:
                return
            if selected_model is not None and selected_model != model_id:
                return
            record["stage"] = stage
            record["updated_at"] = datetime.now(UTC).isoformat()

        try:
            with progress_scope(update, request_id=request_id):
                yield
        finally:
            self._operations.pop(request_id, None)

    def snapshot(self) -> list[dict]:
        now = time.monotonic()
        return [{
            "request_id": record["request_id"], "model_id": record["model_id"],
            "stage": record["stage"], "updated_at": record["updated_at"],
            "elapsed_sec": round(max(0, now - record["started"]), 3),
        } for record in self._operations.values()]
