"""
Synthesis — the core endpoint.

The flow is the whole point of the rewrite made concrete:

    profile_text -> resolve() (pure routing) -> SynthRequest -> scheduler

Routing decides what SHOULD run and how the text must be shaped; the service
layer applies any transform and hands the worker the FINAL string; the worker
renders it or fails. Every response carries the route as a visible chip, and an
unroutable request is a 422 that lists what would work — never a silent
substitution.

`POST /generate` used to block on `scheduler.synthesize()` here directly. It
now does everything through `plan.needs_transform` unchanged — routing is
still pure, still runs once, still fails loudly — then hands the resolved
work to the job queue (`app/jobs/`) and returns 202. The actual synthesis call
that used to be inline is now `app/jobs/handlers/synthesize.py`, unchanged in
substance: same `SynthRequest`, same `apply_audio_effects`, same
`create_generation`. See `app/jobs/__init__.py` for why the queue never
touches `app/inference/scheduler.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ...config import Settings
from ...db import Database
from ...domain.direction import DirectionPlan, Emotion, Level, Rate
from ...domain.direction_analyze import analyze as analyze_direction
from ...domain.language import profile_text
from ...domain.routing import RoutePlan, UrduStrategy, resolve
from ...exceptions import (
    InvalidDirectionPlanError,
    InvalidParamsError,
    NoRouteError,
    ProfileNotFoundError,
)
from ...inference.catalog import ModelCatalog
from ...inference.protocol import SchedulerProtocol
from ...jobs import JobKind, JobRunner
from ...jobs.direction import render as render_direction
from ..deps import get_catalog, get_db, get_job_runner, get_scheduler, get_settings
from ..schemas.jobs import JobStatusResponse
from ..schemas.tts import (
    DirectionPlanIn,
    RouteInfo,
    ScriptDetectRequest,
    ScriptDetectResponse,
    TTSGenerateRequest,
)
from .jobs import build_job_status_response

router = APIRouter(tags=["tts"])


def _apply_direction_overrides(
    plan: DirectionPlan, overrides: DirectionPlanIn | None
) -> DirectionPlan:
    """
    Overlay Advanced-editor prosody edits onto a freshly analyzed plan.

    Segmentation, segment text, and emphasis always come from `plan` (a real,
    just-run `analyze()`) — an override can change how a segment is
    delivered, never what text it contains. An override index `analyze()`
    doesn't currently produce (most often: the text changed since the editor
    fetched its plan) is a 422, not a silently-ignored stale edit — the same
    discipline `InvalidParamsError` already applies to unknown synth params.
    """
    if overrides is None:
        return plan

    by_index = {o.index: o for o in overrides.segments}
    valid_indices = tuple(s.index for s in plan.segments)
    unknown = tuple(i for i in by_index if i not in valid_indices)
    if unknown:
        raise InvalidDirectionPlanError(unknown, valid_indices)

    segments = tuple(
        replace(
            seg,
            emotion=Emotion(by_index[seg.index].emotion),
            intensity=Level(by_index[seg.index].intensity),
            energy=Level(by_index[seg.index].energy),
            rate=Rate(by_index[seg.index].rate),
            pause_after_ms=by_index[seg.index].pause_after_ms,
        )
        if seg.index in by_index
        else seg
        for seg in plan.segments
    )
    return replace(plan, segments=segments)


def _route_info(plan: RoutePlan, catalog: ModelCatalog) -> RouteInfo:
    spec = catalog.get(plan.model_id)
    return RouteInfo(
        model_id=plan.model_id,
        model_display_name=spec.display_name if spec else plan.model_id,
        transform=plan.transform.kind.value,
        lossy=plan.lossy,
        rationale=plan.rationale,
        source_script=plan.source_script.value,
        alternatives=list(plan.alternatives),
        experimental=plan.experimental,
        text_normalizations=list(plan.text_normalizations),
    )


@router.post("/generate", response_model=JobStatusResponse, status_code=202)
async def generate(
    body: TTSGenerateRequest,
    response: Response,
    db: Annotated[Database, Depends(get_db)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
    settings: Annotated[Settings, Depends(get_settings)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> JobStatusResponse:
    profile = await db.get_profile(body.profile_id)
    if profile is None:
        raise ProfileNotFoundError(body.profile_id)

    # Pure routing, still runs synchronously in the request — it's cheap and
    # it MUST fail loudly here rather than after a job row exists. Raises
    # NoRouteError (422, lists what works) / ModelNotFound / AmbiguousScriptError
    # — all mapped to problem+json, none a silent fallback.
    text_profile = profile_text(body.text, body.language)
    plan = resolve(
        text_profile, body.model_id, catalog, _urdu_strategy(body.urdu_strategy),
        allow_experimental=body.allow_experimental,
    )

    spec = catalog.get(plan.model_id)
    assert spec is not None  # resolve() only returns catalog ids

    # A route that still needs a transform has no implementation now that
    # transliteration was dropped (VoxCPM2 reads romanized text directly, so it
    # routes with transform NONE). Fail loudly rather than send raw text a
    # Devanagari-only model cannot read.
    #
    # This branch IS reachable, not defensive: `urdu_strategy="translit"` on
    # Perso-Arabic Urdu makes `_plan_transform` return ARAB_TO_DEVA, and the
    # Hindi target resolves fine, so the plan is built and lands here.
    #
    # 422 via NoRouteError, not 500 via GenerationError: nothing *failed*.
    # Routing chose a shape nothing implements, which is precisely "no model can
    # render this input" — the same condition, and so the same code and body
    # shape, that `resolve()` itself raises for every other unroutable request.
    # A 500 would also be wrong for the client: retrying cannot help.
    if plan.needs_transform:
        raise NoRouteError(
            language=text_profile.language,
            script=text_profile.script.value,
            supported=catalog.supported_pairs(),
            suggestion=(
                f"Rendering this would need a {plan.transform.kind.value!r} text "
                f"transform, which is not implemented."
            ),
        )

    _validate_params(body.params, spec.params, plan.model_id)

    synth_params = dict(body.params)
    if "cfg_value" in spec.params and "cfg_value" not in body.params:
        # Map stability 0-100 to cfg_value 1.5-2.5 (where 70 maps exactly to default 2.0)
        if body.stability <= 70:
            synth_params["cfg_value"] = round(1.5 + (body.stability / 70.0) * 0.5, 2)
        else:
            synth_params["cfg_value"] = round(2.0 + ((body.stability - 70.0) / 30.0) * 0.5, 2)

    # Speech Direction: analyze + render HERE, at enqueue, off the same pure
    # `resolve()`-chosen spec — never re-derived at claim time (golden rule 4,
    # same discipline as the route). `analyze` is a pure heuristic; the segments
    # it renders (per-segment cfg_value/tempo/pause) are stored on the job and
    # synthesized verbatim by the handler. `resolved_text == body.text` here
    # because the transform is NONE (asserted above), so segmenting body.text is
    # segmenting exactly what the model will see.
    segments = None
    if body.apply_direction:
        direction_plan = _apply_direction_overrides(
            analyze_direction(body.text, body.language), body.direction_plan
        )
        rendered = render_direction(direction_plan, spec)
        if rendered.segments:
            segments = [
                {
                    "index": s.index,
                    "text": s.text,
                    # Per-segment direction params (intensity->cfg_value) win over
                    # the global stability-derived cfg; other user params carry through.
                    "params": {**synth_params, **s.params},
                    # Compose the user's global speed with the segment's rate,
                    # clamped to atempo's supported range.
                    "speed": max(0.5, min(2.0, body.speed * s.speed)),
                    "pause_after_ms": s.pause_after_ms,
                }
                for s in rendered.segments
            ]

    # output_path is chosen NOW, before any job row exists, and stored in the
    # job's params — the orphan rule (app/jobs/runner.py): no file is ever
    # written whose path is not already recorded in a job row.
    out_path = settings.generated_dir / f"{uuid.uuid4().hex}.{body.output_format}"

    route = _route_info(plan, catalog)
    job = await runner.enqueue(
        JobKind.SYNTHESIZE,
        params={
            "text": plan.resolved_text,
            "input_text": body.text,
            "language": body.language,
            "reference_audio": str(profile["audio_path"]),
            "reference_text": profile["transcript"] if spec.needs_reference_text else None,
            "output_path": str(out_path),
            "output_format": body.output_format,
            "sample_rate": settings.default_sample_rate,
            "params": synth_params,
            "speed": body.speed,
            "segments": segments,
        },
        # Decided once, here, by the pure resolve() above — never recomputed
        # at claim time. See app/jobs/handlers/synthesize.py.
        route=route.model_dump(),
        profile_id=body.profile_id,
    )

    return await build_job_status_response(job, db, settings, scheduler, response)


@router.post("/detect-script", response_model=ScriptDetectResponse)
async def detect_script(
    body: ScriptDetectRequest,
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> ScriptDetectResponse:
    from ...exceptions import AmbiguousScriptError

    tp = profile_text(body.text, body.language)
    routable, hint, would = True, None, None
    try:
        plan = resolve(tp, None, catalog)
        would = _route_info(plan, catalog)
    except (NoRouteError, AmbiguousScriptError) as exc:
        routable, hint = False, exc.detail
    return ScriptDetectResponse(
        script=tp.script.value,
        script_ratios={s.value: r for s, r in tp.script_ratios.items()},
        is_rtl=tp.is_rtl,
        routable=routable,
        hint=hint,
        would_route_to=would,
    )


def _urdu_strategy(value: str) -> UrduStrategy:
    try:
        return UrduStrategy(value)
    except ValueError:
        return UrduStrategy.NATIVE


def _validate_params(params: dict, declared: dict, model_id: str) -> None:
    unknown = tuple(k for k in params if k not in declared)
    if unknown:
        raise InvalidParamsError(model_id, unknown, tuple(declared))
