"""
Synthesis — the core endpoint.

The flow is the whole point of the rewrite made concrete:

    profile_text -> resolve() (pure routing) -> SynthRequest -> scheduler

Routing decides what SHOULD run and how the text must be shaped; the service
layer applies any transform and hands the worker the FINAL string; the worker
renders it or fails. Every response carries the route as a visible chip, and an
unroutable request is a 422 that lists what would work — never a silent
substitution.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ...audio import apply_audio_effects
from ...config import Settings
from ...db import Database
from ...domain.language import profile_text
from ...domain.routing import RoutePlan, UrduStrategy, resolve
from ...exceptions import (
    GenerationError,
    InvalidParamsError,
    ProfileNotFoundError,
)
from ...inference.catalog import ModelCatalog
from ...inference.protocol import SchedulerProtocol, SynthRequest
from ...inference.spec import RuntimeKind
from ..deps import get_catalog, get_db, get_scheduler, get_settings
from ..media_tokens import make_media_url
from ..schemas.tts import (
    RouteInfo,
    ScriptDetectRequest,
    ScriptDetectResponse,
    TTSGenerateRequest,
    TTSGenerateResponse,
)

router = APIRouter(tags=["tts"])


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
    )


@router.post("/generate", response_model=TTSGenerateResponse)
async def generate(
    body: TTSGenerateRequest,
    response: Response,
    db: Annotated[Database, Depends(get_db)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TTSGenerateResponse:
    profile = await db.get_profile(body.profile_id)
    if profile is None:
        raise ProfileNotFoundError(body.profile_id)

    # Pure routing. Raises NoRouteError (422, lists what works) / ModelNotFound /
    # AmbiguousScriptError — all mapped to problem+json, none a silent fallback.
    text_profile = profile_text(body.text, body.language)
    plan = resolve(text_profile, body.model_id, catalog, _urdu_strategy(body.urdu_strategy))

    spec = catalog.get(plan.model_id)
    assert spec is not None  # resolve() only returns catalog ids

    # A route that still needs a transform has no implementation now that
    # transliteration was dropped (VoxCPM2 reads romanized text directly, so it
    # routes with transform NONE). Fail loudly rather than send raw text a
    # Devanagari-only model cannot read.
    if plan.needs_transform:
        raise GenerationError(
            f"Routing chose a text transform ({plan.transform.kind.value}) that is "
            f"not available; no model can render this input directly."
        )

    _validate_params(body.params, spec.params, plan.model_id)

    out_path = settings.generated_dir / f"{uuid.uuid4().hex}.{body.output_format}"
    result = await scheduler.synthesize(
        SynthRequest(
            model_id=plan.model_id,
            text=plan.resolved_text,
            reference_audio=Path(profile["audio_path"]),
            output_path=out_path,
            reference_text=profile["transcript"] if spec.needs_reference_text else None,
            params=body.params,
            sample_rate=settings.default_sample_rate,
        )
    )

    apply_audio_effects(result.output_path, speed=body.speed, emotion=body.emotion)

    row = await db.create_generation(
        profile_id=body.profile_id, input_text=body.text, language=body.language,
        output_path=str(result.output_path), output_format=body.output_format,
        duration_sec=result.duration_sec, gen_time_sec=result.gen_time_sec,
        model_id=plan.model_id, transform=plan.transform.kind.value, is_lossy=plan.lossy,
        source_script=plan.source_script.value, route_rationale=plan.rationale,
        resolved_text=plan.resolved_text,
    )


    if spec.runtime is RuntimeKind.FAKE:
        # Golden rule: fake audio is never silent about being fake.
        response.headers["X-Fake-Audio"] = "true"

    return TTSGenerateResponse(
        id=row["id"],
        audio_url=make_media_url(
            f"history/{row['id']}", settings.media_token_secret, settings.media_token_ttl_sec
        ),
        duration_sec=result.duration_sec,
        gen_time_sec=result.gen_time_sec,
        rtf=result.rtf,
        language=body.language,
        route=_route_info(plan, catalog),
        created_at=row["created_at"],
    )


@router.post("/detect-script", response_model=ScriptDetectResponse)
async def detect_script(
    body: ScriptDetectRequest,
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> ScriptDetectResponse:
    from ...exceptions import AmbiguousScriptError, NoRouteError

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
