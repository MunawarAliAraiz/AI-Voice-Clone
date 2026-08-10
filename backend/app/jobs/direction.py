"""
AI Voice Clone Studio — Direction renderer & per-model capability declaration.

PURE. No I/O, no clock. Lives in `app/jobs/` (not `app/domain/`) for the same
reason `estimate.py` does: it imports inference types (`ModelSpec`,
`RuntimeKind`), which the domain purity test forbids — but it is otherwise a
plain value-to-value function, unit-testable with literal inputs.

Two jobs:

1. **Declare, per model, what a `DirectionPlan` field means to it** —
   `capability_for(spec)` returns a `CapabilityReport` of HONORED /
   APPROXIMATED / IGNORED per IR field. This is what the UI renders as a chip.
   Golden rule 5: a control the model can't act on must SAY so, visibly, not
   fail silent (the Style-Exaggeration defect).

2. **Render the plan into concrete synth knobs** — `render(plan, spec)` maps
   the IR to per-segment parameters the runtime actually consumes (for VoxCPM:
   `cfg_value`, a tempo multiplier, inter-segment silence; for Chatterbox:
   `exaggeration`/`cfg_weight`, blended from intensity/energy/emotion/rate —
   see docs/PHASE4_CHATTERBOX_DESIGN.md §5). Fields a runtime declares IGNORED
   do not silently leak into the params — exactly the honesty the report
   promises.

This module renders the *preview* (the analyze endpoint returns the report and
the per-segment knob mapping so the user sees it before generating). Wiring the
rendered per-segment knobs into actual multi-segment audio generation is a
later step; it must not weaken golden rule 1, so it is deliberately not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..domain.direction import DirectionPlan, Emotion, Level, Rate
from ..inference.spec import ModelSpec, RuntimeKind

__all__ = [
    "Support",
    "FieldCapability",
    "CapabilityReport",
    "SegmentRender",
    "RenderedDirection",
    "DIRECTION_FIELDS",
    "capability_for",
    "render",
]


class Support(StrEnum):
    """How fully a model can act on one IR field."""

    #: Acted on directly and faithfully (VoxCPM honors segmentation via real
    #: separate synthesis passes).
    HONORED = "honored"
    #: Acted on indirectly / lossily (VoxCPM maps intensity onto `cfg_value`,
    #: which is not an intensity knob but correlates).
    APPROXIMATED = "approximated"
    #: The model has no input for this; it is dropped. Named so the UI can grey
    #: it out rather than pretend it works.
    IGNORED = "ignored"


#: The IR fields a capability report covers. Kept in sync with
#: `DirectedSegment` by `test_direction_fields_cover_ir` (agent-authored).
DIRECTION_FIELDS: tuple[str, ...] = (
    "segmentation",
    "pause_after",
    "rate",
    "emphasis",
    "intensity",
    "emotion",
    "tone",
    "energy",
)


@dataclass(frozen=True, slots=True)
class FieldCapability:
    """One row of the capability chip: a field, its support level, and why."""

    field: str
    support: Support
    #: Human sentence for the tooltip, e.g. "mapped onto guidance (cfg_value)".
    rationale: str


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """What `model_id` does with each IR field. Rendered as a visible chip."""

    model_id: str
    fields: tuple[FieldCapability, ...]

    @property
    def honored(self) -> tuple[str, ...]:
        return tuple(f.field for f in self.fields if f.support is Support.HONORED)

    @property
    def ignored(self) -> tuple[str, ...]:
        return tuple(f.field for f in self.fields if f.support is Support.IGNORED)


@dataclass(frozen=True, slots=True)
class SegmentRender:
    """The concrete knobs one segment renders to, for the routed model."""

    index: int
    text: str
    #: Model-specific generation params (VoxCPM: cfg_value; Chatterbox:
    #: exaggeration/cfg_weight/language_id). Merged over the user's base params
    #: by the caller; only keys the model declares (plus language_id, see
    #: render()'s Chatterbox branch) appear.
    params: dict[str, float | int | str]
    #: ffmpeg tempo multiplier applied post-synth (real DSP, pitch-preserving).
    speed: float
    #: Silence to append after this segment, milliseconds.
    pause_after_ms: int


@dataclass(frozen=True, slots=True)
class RenderedDirection:
    """`render()` output: the capability report plus per-segment knobs."""

    capability: CapabilityReport
    segments: tuple[SegmentRender, ...]


# ── VoxCPM mapping constants ─────────────────────────────────────────────────
# VoxCPM consumes exactly cfg_value (1.0-3.0, "guidance") + inference_timesteps.
# It takes no emotion/tone text conditioning, so those are IGNORED — honestly.
# Intensity is APPROXIMATED onto cfg_value: higher guidance tracks the reference
# more tightly, which reads as more deliberate/intense delivery. This is a
# correlation, not a real intensity knob — hence APPROXIMATED, and the chip says
# so. cfg stays inside the spec's declared [1.0, 3.0] range.

_VOXCPM_CFG_BY_INTENSITY: dict[Level, float] = {
    Level.LOW: 1.6,
    Level.MEDIUM: 2.0,
    Level.HIGH: 2.5,
}

_SPEED_BY_RATE: dict[Rate, float] = {
    Rate.SLOW: 0.9,
    Rate.NORMAL: 1.0,
    Rate.FAST: 1.12,
}

_VOXCPM_FIELDS: tuple[tuple[str, Support, str], ...] = (
    ("segmentation", Support.HONORED, "each segment is a separate synthesis pass"),
    ("pause_after", Support.HONORED, "silence inserted between segments"),
    ("rate", Support.HONORED, "per-segment pitch-preserving tempo (ffmpeg atempo)"),
    ("emphasis", Support.APPROXIMATED, "conveyed via punctuation/casing in passed-through text"),
    ("intensity", Support.APPROXIMATED, "mapped onto guidance (cfg_value)"),
    ("emotion", Support.IGNORED, "VoxCPM takes no emotion conditioning input"),
    ("tone", Support.IGNORED, "VoxCPM takes no tone conditioning input"),
    ("energy", Support.IGNORED, "VoxCPM takes no energy conditioning input"),
)

# ── Chatterbox mapping constants ─────────────────────────────────────────────
# Chatterbox exposes exactly two continuous knobs (exaggeration, cfg_weight) —
# no discrete emotion/tone input. Four IR fields (intensity, energy, emotion,
# rate) plausibly want to drive them; mapping each independently would let the
# last one applied silently win, which is the kind of silent unpredictability
# golden rule 5 exists to prevent. Instead: exaggeration is one blended value
# from (intensity, energy, emotion-arousal); cfg_weight is driven by rate
# alone. Disjoint inputs per knob, so no two fields compete to set the same
# one. See docs/PHASE4_CHATTERBOX_DESIGN.md §5 for the full reasoning — these
# numbers are a starting point pending a by-ear check once a real backend
# exists, same as VoxCPM's own cfg_value range was tuned by ear.

_CHATTERBOX_EXAGGERATION_BASE: dict[tuple[Level, Level], float] = {
    # (intensity, energy) -> base exaggeration
    (Level.LOW, Level.LOW): 0.25,
    (Level.LOW, Level.MEDIUM): 0.35,
    (Level.LOW, Level.HIGH): 0.45,
    (Level.MEDIUM, Level.LOW): 0.35,
    (Level.MEDIUM, Level.MEDIUM): 0.5,
    (Level.MEDIUM, Level.HIGH): 0.6,
    (Level.HIGH, Level.LOW): 0.45,
    (Level.HIGH, Level.MEDIUM): 0.6,
    (Level.HIGH, Level.HIGH): 0.75,
}

#: ANGRY/EXCITED nudge exaggeration up; CALM/SAD/ANXIOUS nudge it down — the
#: same arousal grouping direction_analyze.py's _determine_energy already
#: uses for the IR's own `energy` field. Every other emotion (NEUTRAL, HAPPY,
#: SERIOUS, QUESTIONING) leaves the (intensity, energy) base untouched.
_EXAGGERATION_AROUSAL_DELTA = 0.1

#: Chatterbox's own docs note higher exaggeration speeds up speech and lower
#: cfg_weight compensates with slower, more deliberate pacing — so cfg_weight
#: is driven by rate, inverse to that relationship, independent of the fields
#: feeding exaggeration above.
_CHATTERBOX_CFG_WEIGHT_BY_RATE: dict[Rate, float] = {
    Rate.SLOW: 0.6,
    Rate.NORMAL: 0.5,
    Rate.FAST: 0.35,
}

_CHATTERBOX_FIELDS: tuple[tuple[str, Support, str], ...] = (
    ("segmentation", Support.HONORED, "each segment is a separate synthesis pass"),
    ("pause_after", Support.HONORED, "silence inserted between segments"),
    (
        "rate", Support.APPROXIMATED,
        "drives cfg_weight pre-synth; post-synth ffmpeg atempo still applies on top",
    ),
    (
        "emphasis", Support.APPROXIMATED,
        "conveyed via punctuation/casing in the passed-through text",
    ),
    (
        "intensity", Support.APPROXIMATED,
        "contributes to the blended exaggeration base, not a dedicated knob",
    ),
    (
        "energy", Support.APPROXIMATED,
        "contributes to the blended exaggeration base, not a dedicated knob",
    ),
    (
        "emotion", Support.APPROXIMATED,
        "nudges exaggeration by arousal grouping; not true per-category conditioning",
    ),
    (
        "tone", Support.IGNORED,
        "no analyzer-derived signal yet, and Chatterbox's two knobs are already spoken for",
    ),
)

# Every other runtime: until its renderer is written, it honors only what any
# TTS trivially can (segmentation + pauses + post-synth tempo), and everything
# acoustic is IGNORED. Never claim HONORED without a renderer that proves it —
# that is the unmeasured-catalog-claim defect one layer up.
_GENERIC_FIELDS: tuple[tuple[str, Support, str], ...] = (
    ("segmentation", Support.HONORED, "text is split into separately-synthesized segments"),
    ("pause_after", Support.HONORED, "silence inserted between segments"),
    ("rate", Support.HONORED, "post-synth pitch-preserving tempo (ffmpeg atempo)"),
    ("emphasis", Support.IGNORED, "no renderer maps emphasis for this model yet"),
    ("intensity", Support.IGNORED, "no renderer maps intensity for this model yet"),
    ("emotion", Support.IGNORED, "no emotion conditioning for this model"),
    ("tone", Support.IGNORED, "no tone conditioning for this model"),
    ("energy", Support.IGNORED, "no energy conditioning for this model"),
)


def capability_for(spec: ModelSpec) -> CapabilityReport:
    """
    What `spec` does with each IR field. Pure lookup on `spec.runtime`.

    Returns a report whose `fields` covers exactly `DIRECTION_FIELDS`. VoxCPM
    and Chatterbox each have a real mapping; every other runtime gets the
    honest generic baseline (segmentation/pauses/tempo only) until its own
    renderer exists.
    """
    if spec.runtime is RuntimeKind.VOXCPM:
        rows = _VOXCPM_FIELDS
    elif spec.runtime is RuntimeKind.CHATTERBOX:
        rows = _CHATTERBOX_FIELDS
    else:
        rows = _GENERIC_FIELDS
    return CapabilityReport(
        model_id=spec.id,
        fields=tuple(FieldCapability(field=f, support=s, rationale=r) for f, s, r in rows),
    )


def render(plan: DirectionPlan, spec: ModelSpec) -> RenderedDirection:
    """
    Map a `DirectionPlan` to per-segment synth knobs for `spec`.

    Only params the model declares (`spec.params`) are emitted, and only fields
    the capability report marks HONORED/APPROXIMATED influence them — an IGNORED
    field never silently leaks into the output. For VoxCPM: intensity →
    cfg_value (clamped to declared range), rate → tempo multiplier. For
    Chatterbox: (intensity, energy, emotion-arousal) → exaggeration, rate →
    cfg_weight (both clamped to declared range) — see the constants above and
    docs/PHASE4_CHATTERBOX_DESIGN.md §5.
    """
    cap = capability_for(spec)
    declares_cfg = "cfg_value" in spec.params
    declares_chatterbox = "exaggeration" in spec.params and "cfg_weight" in spec.params
    segments: list[SegmentRender] = []

    for seg in plan.segments:
        params: dict[str, float | int | str] = {}
        if spec.runtime is RuntimeKind.VOXCPM and declares_cfg:
            cfg = _VOXCPM_CFG_BY_INTENSITY.get(seg.intensity, 2.0)
            lo = spec.params["cfg_value"].get("minimum", 1.0)
            hi = spec.params["cfg_value"].get("maximum", 3.0)
            params["cfg_value"] = max(lo, min(hi, cfg))
        elif spec.runtime is RuntimeKind.CHATTERBOX and declares_chatterbox:
            exaggeration = _CHATTERBOX_EXAGGERATION_BASE.get((seg.intensity, seg.energy), 0.5)
            if seg.emotion in (Emotion.ANGRY, Emotion.EXCITED):
                exaggeration += _EXAGGERATION_AROUSAL_DELTA
            elif seg.emotion in (Emotion.CALM, Emotion.SAD, Emotion.ANXIOUS):
                exaggeration -= _EXAGGERATION_AROUSAL_DELTA
            exagg_lo = spec.params["exaggeration"].get("minimum", 0.0)
            exagg_hi = spec.params["exaggeration"].get("maximum", 1.0)
            params["exaggeration"] = round(max(exagg_lo, min(exagg_hi, exaggeration)), 3)

            cfg_weight = _CHATTERBOX_CFG_WEIGHT_BY_RATE.get(seg.rate, 0.5)
            cfgw_lo = spec.params["cfg_weight"].get("minimum", 0.0)
            cfgw_hi = spec.params["cfg_weight"].get("maximum", 1.0)
            params["cfg_weight"] = max(cfgw_lo, min(cfgw_hi, cfg_weight))

            # SynthRequest has no `language` field (routing resolves it once,
            # up front); DirectionPlan does. Injected here so a future
            # ChatterboxBackend.synth() can read it via
            # params.get("language_id", ...), matching voxcpm.py's
            # .get()-defensive style. Pass-through of plan.language assumes
            # Chatterbox's expected codes match this project's LanguageCode
            # values ("hi"/"en"/"ur") — UNVERIFIED until Phase 4b's real
            # backend exists against the actual Chatterbox docs.
            params["language_id"] = plan.language

        speed = _SPEED_BY_RATE.get(seg.rate, 1.0)
        segments.append(
            SegmentRender(
                index=seg.index,
                text=seg.text,
                params=params,
                speed=speed,
                pause_after_ms=seg.pause_after_ms,
            )
        )

    return RenderedDirection(capability=cap, segments=tuple(segments))
