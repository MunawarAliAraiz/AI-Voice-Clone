"""
Jobs — poll, list (the Recent view), cancel.

`POST /api/generate` (in `routers/tts.py`) writes the row; this router only
reads and cancels it. Position and ETA are computed here, per request, from
`Database.list_active_jobs` + `InferenceScheduler.status()` — nothing is
cached, because both change the moment another job claims the GPU slot.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from ...config import Settings
from ...db import Database
from ...exceptions import JobNotFoundError
from ...inference.protocol import SchedulerProtocol
from ...jobs import JobRunner, JobStatus, job_record_from_row
from ...jobs.estimate import estimate_remaining_for_running, estimate_wait_seconds, queue_position
from ...jobs.types import JobRecord
from ..deps import get_db, get_job_runner, get_scheduler, get_settings
from ..media_tokens import make_media_url
from ..schemas.jobs import JobList, JobStatusResponse
from ..schemas.tts import RouteInfo, TTSGenerateResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _route_info(job: JobRecord) -> RouteInfo:
    # `route` is never absent from 'queued' onward — the enqueue path in
    # tts.py always stores it before creating the row. A missing route here
    # is a bug in that path, not a state a client can trigger.
    assert job.route is not None, f"job {job.id} has no route; bug in the enqueue path"
    return RouteInfo(**job.route)


def _is_fake(job: JobRecord) -> bool:
    if job.status is not JobStatus.SUCCEEDED:
        return False
    return bool((job.result or {}).get("is_fake", False))


def _result_or_none(
    job: JobRecord, route: RouteInfo, settings: Settings
) -> TTSGenerateResponse | None:
    if job.status is not JobStatus.SUCCEEDED:
        return None
    result = job.result or {}
    history_id = result["history_id"]
    return TTSGenerateResponse(
        id=history_id,
        # Signed fresh on every read, never stored — media tokens expire
        # (`settings.media_token_ttl_sec`), and a job can be polled long
        # after it finishes.
        audio_url=make_media_url(
            f"history/{history_id}", settings.media_token_secret, settings.media_token_ttl_sec
        ),
        duration_sec=result.get("duration_sec"),
        gen_time_sec=result.get("gen_time_sec", 0.0),
        rtf=result.get("rtf"),
        language=result.get("language", ""),
        route=route,
        created_at=result["created_at"],
    )


def _error_or_none(job: JobRecord) -> dict[str, Any] | None:
    return job.error.to_problem(instance=f"/api/jobs/{job.id}") if job.error else None


def _input_text(job: JobRecord) -> str | None:
    # Set once at enqueue (see routers/tts.py); read straight off the
    # already-decoded params, no extra query.
    text = job.params.get("input_text")
    return str(text) if text else None


async def _queue_position_and_eta(
    job: JobRecord, db: Database, scheduler: SchedulerProtocol
) -> tuple[int | None, float | None]:
    """`position` only while 'queued'; `eta_sec` while 'queued' or 'running'."""
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return None, None

    statuses = await scheduler.status()
    now = time.time()

    if job.status is JobStatus.RUNNING:
        return None, estimate_remaining_for_running(job, statuses, now)

    active = [job_record_from_row(r) for r in await db.list_active_jobs(job.kind.value)]
    running = next((j for j in active if j.status is JobStatus.RUNNING), None)
    queued = [j for j in active if j.status is JobStatus.QUEUED]
    position = queue_position(
        queued_ahead=sum(1 for j in queued if j.id < job.id),
        has_running=running is not None,
    )
    eta_sec = estimate_wait_seconds(statuses, running, queued, now).get(job.id)
    return position, eta_sec


def _build_list_item(
    job: JobRecord, settings: Settings, profile_name: str | None
) -> JobStatusResponse:
    """
    No `position`/`eta_sec` (always `None`, no scheduler round trip) — see
    `JobList`'s docstring: those only matter for the one job a client is
    actively polling, not for a paginated scrollback dominated by terminal
    jobs.
    """
    route = _route_info(job)
    return JobStatusResponse(
        id=job.id,
        kind=job.kind.value,
        status=job.status.value,
        profile_id=job.profile_id,
        profile_name=profile_name,
        input_text=_input_text(job),
        route=route,
        result=_result_or_none(job, route, settings),
        error=_error_or_none(job),
        is_fake=_is_fake(job),
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def build_job_status_response(
    job: JobRecord, db: Database, settings: Settings, scheduler: SchedulerProtocol,
    response: Response,
) -> JobStatusResponse:
    """
    Shared with `routers/tts.py`: `POST /api/generate`'s 202 body is built
    from this SAME function, so the freshly-enqueued response and every later
    poll of the same job are byte-for-byte the same shape.
    """
    route = _route_info(job)
    position, eta_sec = await _queue_position_and_eta(job, db, scheduler)
    is_fake = _is_fake(job)
    if is_fake:
        # Golden rule 1: fake audio is never silent about being fake. Set on
        # the polling GET (the only point a completed job is actually read)
        # — the 202 from POST /generate can't know this yet, nothing has run.
        response.headers["X-Fake-Audio"] = "true"
    profile = await db.get_profile(job.profile_id) if job.profile_id is not None else None
    return JobStatusResponse(
        id=job.id,
        kind=job.kind.value,
        status=job.status.value,
        profile_id=job.profile_id,
        profile_name=profile["name"] if profile else None,
        input_text=_input_text(job),
        route=route,
        position=position,
        eta_sec=eta_sec,
        result=_result_or_none(job, route, settings),
        error=_error_or_none(job),
        is_fake=is_fake,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("", response_model=JobList)
async def list_jobs(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobList:
    rows = await db.list_jobs(limit=page_size, offset=(page - 1) * page_size)
    jobs = [job_record_from_row(r) for r in rows]
    profiles = await db.get_profiles_by_ids(
        [j.profile_id for j in jobs if j.profile_id is not None]
    )
    items = [
        _build_list_item(
            j, settings, profiles[j.profile_id]["name"] if j.profile_id in profiles else None
        )
        for j in jobs
    ]
    return JobList(items=items, total=await db.count_jobs(), page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: int,
    response: Response,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
) -> JobStatusResponse:
    row = await db.get_job(job_id)
    if row is None:
        raise JobNotFoundError(job_id)
    job = job_record_from_row(row)
    return await build_job_status_response(job, db, settings, scheduler, response)


@router.delete("/{job_id}", status_code=204)
async def cancel_job(
    job_id: int, runner: Annotated[JobRunner, Depends(get_job_runner)]
) -> Response:
    await runner.cancel(job_id)  # raises JobNotFoundError (404) / JobNotCancellableError (409)
    return Response(status_code=204)
