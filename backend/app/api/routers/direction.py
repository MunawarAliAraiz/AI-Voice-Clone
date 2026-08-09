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

from fastapi import APIRouter, Depends

from ...domain.direction import DirectionPlan
from ...domain.direction_analyze import analyze
from ...domain.language import profile_text
from ...domain.routing import resolve
from ...inference.catalog import ModelCatalog
from ...jobs.direction import capability_for
from ..deps import get_catalog
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
from .tts import _route_info

router = APIRouter(prefix="/direction", tags=["direction"])


@router.post("/analyze", response_model=DirectionAnalyzeResponse)
async def analyze_direction(
    body: DirectionAnalyzeRequest,
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> DirectionAnalyzeResponse:
    # Pure routing, exactly like `POST /generate` — decides what WOULD run.
    # Raises NoRouteError (422, lists what works) / AmbiguousScriptError,
    # both mapped to problem+json by the installed handler.
    text_profile = profile_text(body.text, body.language)
    plan = resolve(text_profile, None, catalog)

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
