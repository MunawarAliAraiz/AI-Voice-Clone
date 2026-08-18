"""
Speech Direction — read-only preview endpoint.

`POST /api/direction/analyze` turns text + user-declared language into a
`DirectionPlan` (`domain.direction_analyze.analyze`, a pure heuristic) plus the
`CapabilityReport` of the model the text WOULD route to
(`domain.routing.resolve` + `jobs.direction.capability_for`). It generates no
audio and enqueues no job — it exists so the UI can show the honesty chip
("this model honors rate, ignores emotion") BEFORE the user commits to a
generation.

Routing runs the same way `POST /generate` and `POST /detect-script` run it in
`routers/tts.py`: `profile_text -> resolve()`, pure, no I/O, fails loudly.
`NoRouteError` / `AmbiguousScriptError` propagate unchanged to the installed
`AppError` handler (422 problem+json, enumerating what would work) — this
endpoint adds no error handling of its own, on purpose, so an unroutable
preview and an unroutable generation report the identical shape.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ...config import Settings
from ...db import Database
from ...domain.direction import DirectionPlan
from ...domain.direction_analyze import analyze
from ...domain.language import profile_text
from ...domain.routing import resolve
from ...inference.catalog import ModelCatalog
from ...inference.protocol import SchedulerProtocol
from ...jobs import JobKind, JobRunner
from ...jobs.direction import capability_for
from ..deps import (
    get_catalog,
    get_db,
    get_job_runner,
    get_lexicon,
    get_scheduler,
    get_settings,
)
from ..schemas.direction import (
    CapabilityReportOut,
    DirectedSegmentOut,
    DirectionAnalyzeRequest,
    DirectionAnalyzeResponse,
    DirectionPlanOut,
    DirectionSummaryOut,
    EmphasisSpanOut,
    FieldCapabilityOut,
)
from ..schemas.jobs import JobStatusResponse
from .jobs import build_job_status_response
from .tts import _route_info

router = APIRouter(prefix="/direction", tags=["direction"])


@router.post("/analyze", response_model=DirectionAnalyzeResponse)
async def analyze_direction(
    body: DirectionAnalyzeRequest,
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
    lexicon: Annotated[dict[str, str], Depends(get_lexicon)],
) -> DirectionAnalyzeResponse:
    # Pure routing, exactly like `POST /generate` — decides what WOULD run,
    # honoring the caller's model_id/allow_experimental exactly like
    # `tts.py`'s `_route_info` does, instead of always auto-routing. Raises
    # NoRouteError (422, lists what works) / AmbiguousScriptError, both
    # mapped to problem+json by the installed handler.
    text_profile = profile_text(body.text, body.language)
    plan = resolve(
        text_profile, body.model_id, catalog,
        allow_experimental=body.allow_experimental, lexicon=lexicon,
    )

    spec = catalog.get(plan.model_id)
    assert spec is not None  # resolve() only returns catalog ids

    direction_plan = analyze(body.text, body.language)
    capability = capability_for(spec)

    return DirectionAnalyzeResponse(
        plan=_plan_out(direction_plan),
        capability=CapabilityReportOut(
            model_id=capability.model_id,
            model_display_name=spec.display_name,
            fields=[
                FieldCapabilityOut(field=f.field, support=f.support.value, rationale=f.rationale)
                for f in capability.fields
            ],
        ),
        route=_route_info(plan, catalog),
    )


@router.post("/analyze-llm", response_model=JobStatusResponse, status_code=202)
async def analyze_direction_llm(
    body: DirectionAnalyzeRequest,
    response: Response,
    db: Annotated[Database, Depends(get_db)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    settings: Annotated[Settings, Depends(get_settings)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> JobStatusResponse:
    """
    LLM-backed Speech Direction classification, queued — the second,
    higher-quality `DirectionPlan` producer behind the same `analyze()`
    signature/shape as `POST /direction/analyze`'s heuristic, but run on the
    async job queue since a real generate call costs seconds, not
    milliseconds. Reuses `DirectionAnalyzeRequest` (same `{text, language}`
    shape `POST /direction/analyze` takes) rather than inventing a new
    request schema.

    Segmentation/sentence text is decided HERE, ONCE, by the exact same pure
    `analyze()` the heuristic-only endpoint above calls — the enqueued job
    carries only the resulting sentence strings. The handler
    (`jobs/handlers/analyze_llm.py`) classifies exactly those sentences and
    never re-segments `body.text` itself; this is the job-queue analog of
    golden rule 4 that golden rule 8 calls out for routing, applied here to
    segmentation instead.

    `route=None`: this job never calls `resolve()` and never touches the
    audio catalog — the Qwen analyzer is not audio and is not a routable
    `ModelSpec`. The 202 body mirrors `POST /generate`'s shape exactly
    (`build_job_status_response`, shared with `routers/tts.py`); the client
    polls the existing generic `GET /api/jobs/{id}`.
    """
    plan = analyze(body.text, body.language)
    job = await runner.enqueue(
        JobKind.ANALYZE_LLM,
        params={"language": body.language, "sentences": [seg.text for seg in plan.segments]},
        route=None,
        profile_id=None,
    )
    return await build_job_status_response(job, db, settings, scheduler, response)


def _plan_out(plan: DirectionPlan) -> DirectionPlanOut:
    """Serialize the frozen `DirectionPlan` dataclass into its wire schema."""
    return DirectionPlanOut(
        language=plan.language,
        source_script=plan.source_script,
        segments=[
            DirectedSegmentOut(
                text=seg.text,
                index=seg.index,
                emotion=seg.emotion.value,
                tone=seg.tone.value,
                intensity=seg.intensity.value,
                energy=seg.energy.value,
                rate=seg.rate.value,
                emphasis=[EmphasisSpanOut(start=e.start, end=e.end) for e in seg.emphasis],
                pause_after_ms=seg.pause_after_ms,
            )
            for seg in plan.segments
        ],
        summary=DirectionSummaryOut(
            emotion=plan.summary.emotion.value,
            intensity=plan.summary.intensity.value,
            rate=plan.summary.rate.value,
        ),
    )
