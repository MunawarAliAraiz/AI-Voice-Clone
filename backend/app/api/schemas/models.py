"""
AI Voice Clone Studio — Model catalog schemas.

CONTRACT MODULE. Wave 0.

Feeds the dynamic engine/language UI. Everything the picker needs to render a
model — its languages, its license, its residency, and how long the user will
wait — comes from here in one request. The frontend keeps no hardcoded model
list; that is what made the old dropdown advertise Urdu on an engine that could
not speak it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LanguageSupportInfo",
    "ModelSummary",
    "ModelListResponse",
    "LanguageInfo",
    "LanguageListResponse",
    "WarmRequest",
]


class LanguageSupportInfo(BaseModel):
    """One (language, script) pair a model can actually serve."""

    language: str = Field(..., examples=["ur"])
    script: str = Field(..., examples=["arabic"])
    #: Measured in Phase A. Only verified pairs are ever returned to a client.
    verified: bool
    cer: float | None = Field(None, description="Character error rate, Whisper-large-v3.")
    speaker_cosine: float | None = None


class ModelSummary(BaseModel):
    """One model, as the picker sees it."""

    model_config = ConfigDict(protected_namespaces=())

    id: str = Field(..., examples=["f5_openbible_urdu"])
    display_name: str = Field(..., examples=["OpenBible Urdu (F5)"])
    runtime: str = Field(..., examples=["f5", "chatterbox", "voxcpm"])
    #: Shown in the picker. Users of a self-hosted cloner need to know what
    #: they may legally do with the output.
    license: str = Field(..., examples=["CC-BY-SA-4.0", "MIT", "Apache-2.0"])
    languages: list[LanguageSupportInfo]

    #: resident | warm | cold | not_downloaded
    state: str = Field(..., examples=["cold"])
    #: Seconds this request would likely wait before audio starts generating.
    #: The UI shows "cold, ~40s" BEFORE the click, not after.
    est_wait_sec: float
    vram_mb: int
    est_rtf: float | None = None

    #: JSON Schema fragments, keyed by parameter name. The UI renders only the
    #: controls this model declares — no dead knobs.
    params: dict[str, dict] = Field(default_factory=dict)
    needs_reference_text: bool = False
    reference_max_sec: float | None = Field(
        None,
        description=(
            "Reference audio is hard-trimmed to this length by the runtime. "
            "The trim editor exists so WHICH seconds get used is not arbitrary."
        ),
    )
    notes: str = ""


class ModelListResponse(BaseModel):
    models: list[ModelSummary]
    #: Total VRAM budget the scheduler will spend, MB.
    vram_budget_mb: int
    #: How many workers may co-reside.
    max_workers: int


class LanguageInfo(BaseModel):
    """A language the product can actually serve, with its scripts."""

    code: str = Field(..., examples=["ur"])
    display_name: str = Field(..., examples=["Urdu (اردو)"])
    native_name: str = Field(..., examples=["اردو"])
    #: Scripts with at least one verified model. A language with none is not
    #: listed at all rather than listed and broken.
    scripts: list[str] = Field(..., examples=[["arabic", "latin"]])
    model_ids: list[str]
    #: True when this language can only be served through a lossy transform.
    requires_transform: bool = False


class LanguageListResponse(BaseModel):
    languages: list[LanguageInfo]


class WarmRequest(BaseModel):
    """Ask the scheduler to pre-load a model so the next request is resident."""

    model_config = ConfigDict(protected_namespaces=())

    model_id: str
