"""
AI Voice Clone Studio — Speech Direction preview schemas.

Wire types for `POST /api/direction/analyze` (`routers/direction.py`). Mirror
the frozen IR in `domain/direction.py` (`DirectionPlan`, `DirectedSegment`,
`EmphasisSpan`, `DirectionSummary`) and the capability report in
`jobs/direction.py` (`CapabilityReport`, `FieldCapability`) field-for-field,
the same way `schemas/tts.py`'s `RouteInfo` mirrors `domain/routing.RoutePlan`.

Enum fields (`emotion`, `tone`, `intensity`, `energy`, `rate`, `support`) are
plain `str` here, not the domain `StrEnum` types — same choice `RouteInfo`
makes for `transform`/`source_script` — so this schema module stays a wire
contract, independent of the domain/jobs modules it describes.

No `{"status": "ok", "result": ...}` envelope. Failures are RFC 9457
problem+json (see `app/exceptions.py`); this module only shapes success bodies.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .tts import RouteInfo

__all__ = [
    "DirectionAnalyzeRequest",
    "DirectionAnalyzeResponse",
    "DirectionPlanOut",
    "DirectedSegmentOut",
    "DirectionSummaryOut",
    "EmphasisSpanOut",
    "CapabilityReportOut",
    "FieldCapabilityOut",
]


class DirectionAnalyzeRequest(BaseModel):
    """Text + user-declared language to preview a `DirectionPlan` for.

    `model_id`/`allow_experimental` mirror `TTSGenerateRequest` (`schemas/tts.py`)
    exactly, so the capability report reflects whatever model the Composer
    actually has selected — auto-routing (`model_id=None`) never picks an
    unverified/experimental spec, same rule `resolve()` enforces everywhere
    else. No `profile_id`: this is a preview, nothing is synthesized. Also
    reused unchanged by `analyze-llm` (`routers/direction.py`), which never
    calls `resolve()` at all — these two fields are simply unused there.
    """

    text: str = Field(..., min_length=1, max_length=5000, examples=["aap kaise hain?"])
    language: str = Field(..., examples=["ur", "en"])
    model_id: str | None = Field(
        None,
        examples=["omnivoice_urdu"],
        description=(
            "Pin a specific model for the capability report. Refused with "
            "422 if it cannot serve the text."
        ),
    )
    allow_experimental: bool = Field(
        False,
        description=(
            "Required alongside model_id to preview a model that hasn't passed "
            "its own accuracy gate. Ignored when model_id is unset."
        ),
    )


class EmphasisSpanOut(BaseModel):
    """Mirrors `domain.direction.EmphasisSpan`. Offsets into the segment's text."""

    start: int
    end: int


class DirectedSegmentOut(BaseModel):
    """Mirrors `domain.direction.DirectedSegment` field-for-field."""

    text: str
    index: int
    emotion: str = Field(
        ...,
        examples=["neutral", "happy", "sad", "angry", "excited", "calm", "serious", "questioning"],
    )
    tone: str = Field(..., examples=["neutral", "warm", "firm", "soft"])
    intensity: str = Field(..., examples=["low", "medium", "high"])
    energy: str = Field(..., examples=["low", "medium", "high"])
    rate: str = Field(..., examples=["slow", "normal", "fast"])
    emphasis: list[EmphasisSpanOut] = Field(default_factory=list)
    pause_after_ms: int


class DirectionSummaryOut(BaseModel):
    """Mirrors `domain.direction.DirectionSummary`. Derived from `segments`."""

    emotion: str
    intensity: str
    rate: str


class DirectionPlanOut(BaseModel):
    """Mirrors `domain.direction.DirectionPlan` field-for-field."""

    language: str
    source_script: str = Field(..., examples=["latin", "arabic", "devanagari"])
    segments: list[DirectedSegmentOut] = Field(default_factory=list)
    summary: DirectionSummaryOut


class FieldCapabilityOut(BaseModel):
    """Mirrors `jobs.direction.FieldCapability`. One row of the honesty chip."""

    field: str = Field(..., examples=["segmentation", "pause_after", "rate", "emphasis",
                                       "intensity", "emotion", "tone", "energy"])
    support: str = Field(..., examples=["honored", "approximated", "ignored"])
    rationale: str = Field(..., examples=["mapped onto guidance (cfg_value)"])


class CapabilityReportOut(BaseModel):
    """
    What the routed model does with each `DirectionPlan` field.

    Mirrors `jobs.direction.CapabilityReport`, plus `model_display_name` (not
    on the dataclass — that lives on `ModelSpec`) so the UI chip needs no
    second lookup, the same reason `RouteInfo` carries both `model_id` and
    `model_display_name`.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_id: str = Field(..., examples=["voxcpm2"])
    model_display_name: str = Field(..., examples=["VoxCPM 2"])
    fields: list[FieldCapabilityOut]


class DirectionAnalyzeResponse(BaseModel):
    """
    `POST /api/direction/analyze`'s body: the plan, its capability report for
    the routed model, and the route itself — the same `RouteInfo` chip every
    other endpoint renders, so the UI needs no special case for this preview.
    """

    plan: DirectionPlanOut
    capability: CapabilityReportOut
    route: RouteInfo
