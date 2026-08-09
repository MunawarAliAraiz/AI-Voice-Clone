"""
AI Voice Clone Studio — TTS request/response schemas.

CONTRACT MODULE. Wave 0.

No `{"status": "ok", "result": ...}` envelope. Success bodies are the resource.
Failures are RFC 9457 problem+json (see `app/exceptions.py`).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RouteInfo",
    "TTSGenerateRequest",
    "TTSGenerateResponse",
    "ScriptDetectRequest",
    "ScriptDetectResponse",
]


class RouteInfo(BaseModel):
    """
    What actually happened to the request, echoed back for display.

    The UI renders this as a chip beside the player. It is not diagnostics — it
    is the mechanism by which a user is never left wondering why their Urdu came
    out sounding like Hindi. If the chip is ever hidden, the guarantee is gone.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_id: str = Field(..., examples=["f5_openbible_urdu"])
    model_display_name: str = Field(..., examples=["OpenBible Urdu (F5)"])
    transform: str = Field(
        ..., examples=["none", "roman_to_deva", "arab_to_deva"],
        description="Text transformation applied before synthesis.",
    )
    lossy: bool = Field(
        ...,
        description=(
            "True when the transform may have altered pronunciation. "
            "Perso-Arabic omits short vowels, so romanizing it guesses them."
        ),
    )
    rationale: str = Field(
        ...,
        examples=["Roman Urdu transliterated to Devanagari and rendered by IndicF5"],
        description="User-facing prose. Displayed verbatim.",
    )
    source_script: str = Field(..., examples=["latin", "arabic", "devanagari"])
    alternatives: list[str] = Field(
        default_factory=list,
        description="Other model ids that could have served this request.",
    )


class TTSGenerateRequest(BaseModel):
    """
    Request to synthesize speech.

    Note what is absent: there is no `engine: "auto"` default. `language` is
    REQUIRED because the user declares it and the server detects only the
    script; `model_id` is optional but, when given, is honored or refused —
    never silently swapped.

    Also absent: `emotion` and `style`. Nine presets that resolved to an atempo
    multiplier, with text preprocessing that was a no-op on Urdu and Hindi — the
    target languages — is a feature surface with no feature behind it. Real
    per-model knobs go in `params`, validated against the spec's declared schema.
    """

    model_config = ConfigDict(protected_namespaces=())

    text: str = Field(..., min_length=1, max_length=5000, examples=["السلام علیکم، آپ کیسے ہیں؟"])
    profile_id: int = Field(..., examples=[1])
    language: str = Field(
        ...,
        examples=["ur", "hi", "en"],
        description="Declared by the user. Never inferred — (ur, latin) IS Roman Urdu.",
    )
    model_id: str | None = Field(
        None,
        examples=["f5_openbible_urdu"],
        description="Pin a specific model. Refused with 422 if it cannot serve the text.",
    )
    urdu_strategy: str = Field(
        "native",
        examples=["native", "translit"],
        description=(
            "Perso-Arabic Urdu only. 'native' uses the Urdu checkpoint; "
            "'translit' routes via Devanagari to a Hindi model (lossy, often "
            "better prosody on conversational text)."
        ),
    )
    output_format: str = Field("wav", examples=["wav", "mp3"])
    speed: float = Field(
        1.0,
        ge=0.5,
        le=2.0,
        description="Speed multiplier for synthesized audio. 1.0 is original, <1.0 is slower, >1.0 is faster.",
    )
    stability: int = Field(
        70,
        ge=0,
        le=100,
        description="Speech stability percentage (0-100). Default 70. Higher values increase consistency, lower values increase expressiveness.",
    )
    params: dict[str, float | int | str | bool] = Field(
        default_factory=dict,
        description="Model-specific knobs. Validated against the spec; unknown keys are a 422.",
    )
    apply_direction: bool = Field(
        False,
        description=(
            "Apply Speech Direction. When true, the text is analyzed into "
            "per-segment prosody (intensity->cfg_value, rate->tempo, "
            "inter-segment pauses) and each segment is synthesized separately, "
            "then joined. Only fields the routed model HONORS/APPROXIMATES take "
            "effect — see GET /api/direction/analyze for the capability report. "
            "Default false keeps the original single-shot behavior."
        ),
    )


class TTSGenerateResponse(BaseModel):
    """A completed generation."""

    id: int
    #: Signed, expiring URL. The client NEVER constructs a media URL itself —
    #: `<audio>` cannot send auth headers, so the signature travels in the query.
    audio_url: str = Field(..., examples=["/api/media/history/42?t=abc123.1754300000"])
    duration_sec: float | None
    gen_time_sec: float
    rtf: float | None = Field(
        None, description="gen_time / duration. <1.0 is faster than realtime."
    )
    language: str
    #: Never optional. Every generation states what produced it.
    route: RouteInfo
    created_at: datetime
    #: 1 for a normal single-shot generation; >1 when Speech Direction was
    #: applied and the clip was rendered from several separately-synthesized,
    #: pause-joined segments (see `apply_direction` on the request).
    segment_count: int = 1


class ScriptDetectRequest(BaseModel):
    """Live script detection for the editor, so the warning precedes generation."""

    text: str = Field(..., max_length=5000)
    language: str = Field(..., examples=["ur", "hi", "en"])


class ScriptDetectResponse(BaseModel):
    """
    Detected script plus routability, without synthesizing anything.

    Powers the debounced textarea warning and the `dir="rtl"` decision, which
    keys off `script` and never off `language == 'ur'` — the latter wrongly
    right-aligns Roman Urdu.
    """

    script: str = Field(..., examples=["arabic", "latin", "devanagari", "mixed", "unknown"])
    script_ratios: dict[str, float]
    is_rtl: bool
    routable: bool
    #: Populated when routable is False: what the user could change.
    hint: str | None = None
    would_route_to: RouteInfo | None = None
