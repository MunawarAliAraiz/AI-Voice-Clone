"""
AI Voice Clone Studio — Heuristic Speech-Direction analyzer.

CONTRACT MODULE (signature frozen). Phase 2. PURE — stdlib only, no I/O, no
clock, no inference imports.

Turns raw text into a `DirectionPlan` (see `direction.py`) using only signals a
zero-dependency, offline heuristic can read:

  * **Segmentation** — reuse `domain.text.split_sentences` with the detected
    script's terminators (Urdu ۔, Devanagari । included). One sentence → one
    `DirectedSegment`.
  * **Emotion** — a per-language keyword lexicon (Urdu Roman + Perso-Arabic,
    Hindi, English) plus punctuation: `!` leans excited/angry, `?` →
    QUESTIONING, `...`/`—` → CALM/serious. NEUTRAL when nothing matches — never
    guess to seem clever.
  * **Intensity / energy** — `!` count, ALL-CAPS runs, intensifier words
    ("very", "bohat", "बहुत") raise the level; hedges lower it.
  * **Rate** — long clause-dense sentences → SLOW; short exclamatory → FAST.
  * **Emphasis** — ALL-CAPS runs, ``*asterisks*``, terminal ``!`` → EmphasisSpan
    offsets into the SEGMENT's text (half-open [start, end)).
  * **pause_after_ms** — from terminating punctuation strength: `.`/`۔`/`।`
    → DEFAULT_PAUSE_MS, `?`/`!` a bit more, `...`/paragraph break more still.

`summary` is the dominant emotion/intensity/rate ACROSS segments (for simple
mode) and MUST be derived from `segments`, never set independently.

An LLM-backed analyzer is a planned second implementation behind a settings
flag with this exact signature — keep the contract stable so it drops in.

⚠️ The body below is a FALLBACK placeholder (one neutral segment per sentence)
so the package imports and the API works while the heuristic is built. Replacing
this body with the real lexicon logic — and adding `test_direction_analyze.py`
covering all three languages — is this module's implementation task. Do not
change the signature or the return type.
"""

from __future__ import annotations

from .direction import (
    DirectedSegment,
    DirectionPlan,
    DirectionSummary,
)
from .language import detect_script
from .text import split_sentences

__all__ = ["analyze"]


def analyze(text: str, language: str) -> DirectionPlan:
    """
    Analyze `text` (user-declared `language`) into a model-agnostic
    `DirectionPlan`. Pure and deterministic: same input → same output.

    Empty / whitespace-only input returns an empty plan (no segments), never a
    zero-length segment.
    """
    script, _ratios = detect_script(text)
    sentences = split_sentences(text, script)

    segments = tuple(
        DirectedSegment(text=sentence, index=i)
        for i, sentence in enumerate(sentences)
    )

    return DirectionPlan(
        language=language,
        source_script=script.value,
        segments=segments,
        summary=DirectionSummary(),
    )
