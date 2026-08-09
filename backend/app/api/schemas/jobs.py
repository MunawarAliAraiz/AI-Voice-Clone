"""
AI Voice Clone Studio — Job queue schemas.

`JobStatusResponse` is ONE shape used for `POST /api/generate`'s 202, for
`GET /api/jobs/{id}` polling, and for each item in `GET /api/jobs` (the
Recent view) — a client that polls a job and one that lists jobs read the
same fields.

This is NOT the `{"status": "ok", "result": ...}` envelope CLAUDE.md forbids.
`status` here is domain state (queued/running/succeeded/failed/cancelled),
never transport state: a bad job id is a 404 problem+json response, never a
200 with an `error` field. Transport status only ever says whether the JOB
RESOURCE was readable; `status` inside it says what the job is doing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .tts import RouteInfo, TTSGenerateResponse

__all__ = ["JobStatusResponse", "JobList"]


class JobStatusResponse(BaseModel):
    """A job's current state."""

    model_config = ConfigDict(protected_namespaces=())

    id: int
    kind: str = Field(..., examples=["synthesize"])
    status: str = Field(..., examples=["queued", "running", "succeeded", "failed", "cancelled"])
    profile_id: int | None = None
    profile_name: str | None = None
    #: The pre-transform text the user typed, read straight from the job's
    #: stored params (set once at enqueue) — no extra query needed for it.
    input_text: str | None = None

    #: Never absent, from 'queued' onward. Routing (`resolve()`) is pure, so
    #: it costs nothing to know it before the job has even started — the UI's
    #: route chip renders immediately, exactly as it does for a completed
    #: generation.
    route: RouteInfo

    #: 0-indexed jobs strictly ahead of this one in its kind's queue (a
    #: running job, if any, counts as 1). Populated only while status is
    #: 'queued'.
    position: int | None = None
    #: Seconds. A UI estimate, not a promise — see `app/jobs/estimate.py`.
    #: Populated while status is 'queued' or 'running'.
    eta_sec: float | None = None

    #: Populated once status == 'succeeded'. The SAME shape the old
    #: synchronous `/generate` returned, so `ResultCard`/`AudioPlayer` need no
    #: frontend change — `audio_url` is signed fresh on every read, never
    #: stored (media tokens expire; storing one would eventually hand back a
    #: dead link).
    result: TTSGenerateResponse | None = None
    #: Populated once status == 'failed'. Exactly what `AppError.to_problem()`
    #: produces (`app/exceptions.py`) — the frontend's existing problem+json
    #: handling (`services/api.ts`) applies to this field unchanged.
    error: dict[str, Any] | None = None

    #: True only once a 'succeeded' job's audio came from the fake runtime.
    #: Golden rule 1: fake audio is never silent about being fake. The old
    #: synchronous endpoint set `X-Fake-Audio` on the response directly; here
    #: `GET /api/jobs/{id}` sets BOTH the header and this field for such a
    #: job, because a proxy in front of this API (the Cloudflare Worker) can
    #: strip headers but cannot silently drop a response field the frontend
    #: reads.
    is_fake: bool = False

    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobList(BaseModel):
    """
    The Recent view: `GET /api/jobs`, newest-first, paginated — same shape and
    pagination convention as `HistoryList`. Deliberately does not compute
    `position`/`eta_sec` per item (both stay `None`): those are only
    meaningful for the one job a client is actively polling, not for a
    scrollback list dominated by terminal jobs — see `routers/jobs.py`.
    """

    items: list[JobStatusResponse]
    total: int
    page: int
    page_size: int
