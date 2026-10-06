"""
Jobs — poll, list (the Recent view), cancel.

`POST /api/generate` (in `routers/tts.py`) writes the row; this router only
reads and cancels it. Position and ETA are computed here, per request, from
`Database.list_active_jobs` + `InferenceScheduler.status()` — nothing is
cached, because both change the moment another job claims the GPU slot.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from ...config import Settings
from ...db import Database
from ...exceptions import JobNotFoundError, JobNotRetryableError
from ...inference.protocol import ModelStatus, SchedulerProtocol
from ...jobs import JobKind, JobRunner, JobStatus, job_record_from_row
from ...jobs.estimate import estimate_remaining_for_running, estimate_wait_seconds
from ...jobs.types import JobRecord
from ..deps import get_db, get_job_runner, get_scheduler, get_settings, require_cloud_ready
from ..media_tokens import make_media_url
from ..schemas.jobs import JobList, JobStatusResponse
from ..schemas.tts import RouteInfo, TTSGenerateResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _route_info(job: JobRecord) -> RouteInfo | None:
    """
    `route` is set at enqueue for job kinds that route through the audio
    catalog. Currently only SYNTHESIZE does — the enqueue path in tts.py
    always stores one before creating the row, so a SYNTHESIZE job with no
    route would be a bug in that path, not a state a client can trigger.
    ANALYZE_LLM jobs never call `resolve()` (the Qwen analyzer is not audio
    and is not reachable from `domain/routing.py`) and store `route=None`
    on purpose — see `routers/direction.py`'s analyze-llm endpoint.
    """
    if job.route is None:
        return None
    return RouteInfo(**job.route)


def _is_fake(job: JobRecord) -> bool:
    if job.status is not JobStatus.SUCCEEDED:
        return False
    return bool((job.result or {}).get("is_fake", False))


def _result_or_none(
    job: JobRecord, route: RouteInfo | None, settings: Settings
) -> TTSGenerateResponse | dict[str, Any] | None:
    if job.status is not JobStatus.SUCCEEDED:
        return None
    result = job.result or {}
    if job.kind is not JobKind.SYNTHESIZE:
        # Non-audio job kinds (currently only ANALYZE_LLM) have no RoutePlan
        # and write no `generation_history` row — their result is the opaque
        # dict the handler produced (`jobs/handlers/analyze_llm.py`),
        # returned as-is. A future frontend pass reads e.g. `result.rows`.
        return result
    assert route is not None, (
        f"job {job.id} is a SYNTHESIZE job with no route; bug in the enqueue path"
    )
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
        segment_count=result.get("segment_count", 1),
    )


def _error_or_none(job: JobRecord) -> dict[str, Any] | None:
    return job.error.to_problem(instance=f"/api/jobs/{job.id}") if job.error else None


def _input_text(job: JobRecord) -> str | None:
    # Set once at enqueue (see routers/tts.py); read straight off the
    # already-decoded params, no extra query.
    text = job.params.get("input_text") or job.params.get("text")
    if not text and isinstance(job.params.get("texts"), list):
        text = '\n'.join(str(t) for t in job.params['texts'])
    if not text and isinstance(job.params.get('sentences'), list):
        text = '\n'.join(str(t) for t in job.params['sentences'])
    return str(text) if text else None


def _title(job: JobRecord) -> str | None:
    """Same source as `_input_text`. A job enqueued before titles existed has
    no key here and reads as None, which is what the UI expects."""
    title = job.params.get("title")
    return str(title) if title else None


async def _queue_position_and_eta(
    job: JobRecord, db: Database, scheduler: SchedulerProtocol,
    *, statuses: tuple[ModelStatus, ...] | None = None,
    active: Sequence[JobRecord] | None = None, cloud: bool = False,
) -> tuple[int | None, float | None]:
    """`position` only while 'queued'; `eta_sec` while 'queued' or 'running'."""
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return None, None

    if active is None:
        active = [job_record_from_row(r) for r in await db.list_active_jobs(job.kind.value)]
    queued = [j for j in active if j.status is JobStatus.QUEUED]
    running_jobs = [j for j in active if j.status is JobStatus.RUNNING]
    position = (next((i for i, j in enumerate(queued) if j.id == job.id), 0)
                + len(running_jobs)) if job.status is JobStatus.QUEUED else None
    # Legacy local estimates do not include Pod startup, cache checks or helper
    # model loading. Cloud work has no trustworthy ETA until stage timings exist.
    if cloud or job.kind is JobKind.ANALYZE_LLM or len(running_jobs) > 1:
        return position, None
    statuses = statuses if statuses is not None else await scheduler.status()
    now = time.time()

    if job.status is JobStatus.RUNNING:
        return None, estimate_remaining_for_running(job, statuses, now)

    running = next((j for j in active if j.status is JobStatus.RUNNING), None)
    eta_sec = estimate_wait_seconds(statuses, running, queued, now).get(job.id)
    return position, eta_sec


def _timing(job: JobRecord, eta: float | None, *, cloud: bool) -> dict[str, Any]:
    active = job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
    elapsed = None
    if job.started_at:
        started = datetime.fromisoformat(job.started_at.replace('Z', '+00:00'))
        ended = (datetime.fromisoformat(job.finished_at.replace('Z', '+00:00'))
                 if job.finished_at else datetime.now(UTC))
        elapsed = max(0.0, (ended - started).total_seconds())
    state = 'not_applicable' if not active else 'estimated' if eta is not None else (
        'overdue' if not cloud and job.kind is not JobKind.ANALYZE_LLM
        and job.status is JobStatus.RUNNING else 'unknown')
    return {'eta_state': state, 'elapsed_sec': elapsed,
            'phase': job.phase if active else None,
            'phase_updated_at': datetime.fromisoformat(job.phase_updated_at.replace('Z', '+00:00'))
            if active and job.phase_updated_at else None,
            'phase_model_id': job.phase_model_id if active else None}


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
        retry_of_job_id=job.retry_of_job_id,
        profile_name=profile_name,
        input_text=_input_text(job),
        title=_title(job),
        route=route,
        result=_result_or_none(job, route, settings),
        error=_error_or_none(job),
        is_fake=_is_fake(job),
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        **_timing(job, None, cloud=True),
    )


async def build_job_status_response(
    job: JobRecord,
    db: Database,
    settings: Settings,
    scheduler: SchedulerProtocol,
    response: Response,
) -> JobStatusResponse:
    """
    Shared with `routers/tts.py`: `POST /api/generate`'s 202 body is built
    from this SAME function, so the freshly-enqueued response and every later
    poll of the same job are byte-for-byte the same shape.
    """
    route = _route_info(job)
    cloud = bool(settings.desktop_static_dir or settings.remote_worker_url)
    position, eta_sec = await _queue_position_and_eta(job, db, scheduler, cloud=cloud)
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
        retry_of_job_id=job.retry_of_job_id,
        input_text=_input_text(job),
        title=_title(job),
        route=route,
        position=position,
        eta_sec=eta_sec,
        **_timing(job, eta_sec, cloud=cloud),
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


@router.get('/activity', response_model=JobList)
async def activity_jobs(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    tracked_id: Annotated[list[int] | None, Query(max_length=256)] = None,
) -> JobList:
    """Complete active feed with terminal transitions for older watched jobs."""
    jobs = [job_record_from_row(r) for r in await db.list_activity_jobs(tracked_id or [])]
    profiles = await db.get_profiles_by_ids([j.profile_id for j in jobs if j.profile_id])
    cloud = bool(settings.desktop_static_dir or settings.remote_worker_url)
    statuses = () if cloud or not any(j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
                                     for j in jobs) else await scheduler.status()
    items = []
    active_by_kind = {
        kind: sorted((j for j in jobs if j.kind == kind and j.status in
                      (JobStatus.QUEUED, JobStatus.RUNNING)), key=lambda j: (-j.priority, j.id))
        for kind in JobKind
    }
    for job in jobs:
        item = _build_list_item(job, settings,
            profiles[job.profile_id]['name'] if job.profile_id in profiles else None)
        position, eta = await _queue_position_and_eta(
            job, db, scheduler, statuses=statuses, active=active_by_kind[job.kind], cloud=cloud)
        items.append(item.model_copy(update={'position': position, 'eta_sec': eta,
                                            **_timing(job, eta, cloud=cloud)}))
    return JobList(items=items, total=len(items), page=1, page_size=len(items))


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


@router.post(
    "/{job_id}/retry",
    response_model=JobStatusResponse,
    status_code=202,
    dependencies=[Depends(require_cloud_ready)],
)
async def retry_job(
    job_id: int,
    response: Response,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> JobStatusResponse:
    """
    Re-enqueue a settled job from its own stored parameters.

    WHY THIS EXISTS: `JOB_INTERRUPTED` — the failure a server restart leaves
    behind — says "Re-submit it", and until now there was nothing to click.
    The text had to be retyped from a row that was still showing it.

    GOLDEN RULE 8, PRECISELY: the stored `route_json` is REUSED, not
    recomputed. Calling `resolve()` again here would be rule 4's bug wearing a
    retry button — a job that comes back on a different model than the one the
    user was told about. If the catalog changed underneath, the handler's
    existing `ModelNotFoundError` path fails it loudly, which is the correct
    outcome and not something to paper over here.

    A NEW ROW, not a resurrection of the old one. History stays truthful: the
    interrupted attempt remains failed and visible, and the retry is its own
    row with its own outcome. Rewriting the original to 'queued' would erase
    the evidence that anything went wrong.
    """
    row = await db.get_job(job_id)
    if row is None:
        raise JobNotFoundError(job_id)
    original = job_record_from_row(row)

    if not original.status.is_terminal:
        raise JobNotRetryableError(job_id, str(original.status))

    params = dict(original.params)
    if original.kind == JobKind.SYNTHESIZE:
        # A FRESH output path. The old one may already hold a partial file, or
        # may have been swept by the startup orphan reaper; either way, the
        # orphan rule requires the path to be recorded on the row that writes
        # it, so a retry gets its own.
        fmt = params.get("output_format") or "wav"
        params["output_path"] = str(settings.generated_dir / f"{uuid.uuid4().hex}.{fmt}")

    job = await runner.enqueue(
        original.kind,
        params=params,
        route=original.route,
        profile_id=original.profile_id,
        # The link back. Without it the original row keeps offering "Try
        # again" after it has already been retried, and four clicks produce
        # four identical queued jobs with nothing to say they are the same
        # attempt. The new row stays a NEW row (see above) -- this records
        # what it came from, it does not resurrect anything.
        retry_of_job_id=original.id,
    )
    return await build_job_status_response(job, db, settings, scheduler, response)


@router.delete("/{job_id}", status_code=204)
async def cancel_job(
    job_id: int, runner: Annotated[JobRunner, Depends(get_job_runner)]
) -> Response:
    await runner.cancel(job_id)  # raises JobNotFoundError (404) / JobNotCancellableError (409)
    return Response(status_code=204)
