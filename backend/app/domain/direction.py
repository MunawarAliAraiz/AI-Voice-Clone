"""
AI Voice Clone Studio — Speech Direction IR (the "what the speaker is doing").

CONTRACT MODULE. Phase 2. Pure by construction — stdlib only, no I/O, no clock,
no inference imports (see `test_domain_is_pure` / `test_domain_never_imports_inference`).

This is the *intermediate representation* between raw text and a renderer. The
analyzer (`direction_analyze.py`) produces a `DirectionPlan` from text; a
per-model renderer (`app/jobs/direction.py`, which may import inference types)
turns that plan into concrete synth knobs AND declares, per field, whether the
model **honors / approximates / ignores** it.

That honest-declaration step is the whole point, and the reason this is Phase 2
rather than another slider: golden rule 5 (no silent fallback) applies to
prosody controls exactly as it does to routing. A "sad" control that a model
silently can't act on is the Style-Exaggeration mistake again. The IR is
model-agnostic; the renderer's capability report is what the UI renders as a
visible chip, exactly like `route: {lossy, rationale}`.

The IR is deliberately small and discrete. Every field here is something a
zero-dependency heuristic analyzer can infer from punctuation, casing, and a
per-language keyword lexicon — and something *some* real runtime can act on.
Nothing goes in this IR that no planned renderer could ever honor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Emotion",
    "Tone",
    "Level",
    "Rate",
    "EmphasisSpan",
    "DirectedSegment",
    "DirectionSummary",
    "DirectionPlan",
    "DEFAULT_PAUSE_MS",
]


class Emotion(StrEnum):
    """
    Coarse affect label. Kept small and language-neutral: each value must be
    inferrable from a keyword lexicon in Urdu/Hindi/English and mappable to
    *some* renderer's controls. `NEUTRAL` is the default and the honest answer
    when nothing in the text signals otherwise — never guess an emotion to
    seem clever.
    """

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANXIOUS = "anxious"
    ANGRY = "angry"
    EXCITED = "excited"
    CALM = "calm"
    SERIOUS = "serious"
    QUESTIONING = "questioning"


class Tone(StrEnum):
    """Delivery colour, orthogonal to emotion (a 'happy' line can be 'soft')."""

    NEUTRAL = "neutral"
    WARM = "warm"
    FIRM = "firm"
    SOFT = "soft"
    #: Narrator / sports-commentator delivery style. Not yet analyzer-derived —
    #: see direction_analyze.py's module docstring for why.
    NARRATIVE = "narrative"


class Level(StrEnum):
    """A discrete low/medium/high. Used for both intensity and energy.

    Discrete on purpose: a heuristic analyzer cannot honestly produce a
    calibrated 0.0-1.0 float, and a fake-precise slider is worse than three
    honest buckets. Renderers map these to their own numeric knobs.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Rate(StrEnum):
    """Speaking rate, mapped by renderers to a real tempo multiplier."""

    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


#: Default inter-segment pause (milliseconds) at a sentence boundary. Renderers
#: that honor `pause_after_ms` insert this much silence between segments;
#: stronger punctuation (ellipsis, paragraph break) yields more, set by the
#: analyzer, not here.
DEFAULT_PAUSE_MS = 120


@dataclass(frozen=True, slots=True)
class EmphasisSpan:
    """
    A stretch of a segment's text the speaker leans on.

    Character offsets are into the *segment's* `text`, not the whole document —
    a segment is the unit a renderer works with. `[start, end)`, half-open.
    Sources a heuristic analyzer can use: ALL-CAPS runs, ``*asterisks*``,
    and terminal ``!``. A renderer that only "approximates" emphasis may render
    these by re-casing or punctuating the passed-through text rather than by
    real acoustic stress.
    """

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class DirectedSegment:
    """
    One span of text with its direction. The unit a renderer synthesizes.

    Segmentation itself is meaningful direction: it decides where prosody can
    reset and where pauses land. It reuses `domain.text.split_sentences`, so
    the same script-aware terminators (Urdu ۔, Devanagari ।) apply.
    """

    text: str
    index: int
    emotion: Emotion = Emotion.NEUTRAL
    tone: Tone = Tone.NEUTRAL
    intensity: Level = Level.MEDIUM
    energy: Level = Level.MEDIUM
    rate: Rate = Rate.NORMAL
    #: Spans within THIS segment's text to emphasize. Empty when none detected.
    emphasis: tuple[EmphasisSpan, ...] = ()
    #: Silence to insert AFTER this segment, in milliseconds. Derived from the
    #: strength of the terminating punctuation by the analyzer.
    pause_after_ms: int = DEFAULT_PAUSE_MS


@dataclass(frozen=True, slots=True)
class DirectionSummary:
    """
    The 3-ish controls "simple mode" shows: the document's dominant direction,
    so the UI does not have to render a per-segment editor by default. The
    Advanced tab exposes the full per-segment `DirectionPlan.segments`.
    """

    emotion: Emotion = Emotion.NEUTRAL
    intensity: Level = Level.MEDIUM
    rate: Rate = Rate.NORMAL


@dataclass(frozen=True, slots=True)
class DirectionPlan:
    """
    The analyzer's output: model-agnostic direction for a whole input.

    `source_script` is the detected script (via `domain.language`), carried so
    a renderer and the UI don't re-detect it. `summary` is derived from
    `segments` — the dominant emotion/intensity/rate across them — for simple
    mode; it must be consistent with the segments, never independently set.
    """

    language: str
    source_script: str
    segments: tuple[DirectedSegment, ...] = ()
    summary: DirectionSummary = field(default_factory=DirectionSummary)

    @property
    def is_empty(self) -> bool:
        return not self.segments
