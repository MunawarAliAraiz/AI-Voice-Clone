"""
AI Voice Clone Studio — Script-aware text segmentation.

CONTRACT MODULE. Wave 0. Implementations are B3's.

Extracted from `engines/f5_tts.py:99`, which handled the Urdu full stop `۔`
correctly but kept that knowledge trapped inside one engine class where the
other two runtimes could not reach it. Every runtime needs chunking; only one
had it.

Extended here beyond what F5 had, since Hindi and Urdu are the target languages:

    ۔  U+06D4  Urdu full stop
    ؟  U+061F  Arabic question mark
    ،  U+060C  Arabic comma
    ।  U+0964  Devanagari danda
    ॥  U+0965  Devanagari double danda

Must be tested on all three scripts. A splitter that only knows `.` produces one
enormous chunk for any Urdu or Hindi input, which then blows past the model's
frame limit and truncates mid-sentence — silently, because nothing checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from .language import Script

__all__ = [
    "SENTENCE_TERMINATORS",
    "TextChunk",
    "split_sentences",
    "chunk_for_synthesis",
    "normalize_whitespace",
]


#: Sentence-final punctuation by script. Latin's set is deliberately narrow:
#: abbreviations and decimals make `.` unreliable, and B3's implementation is
#: expected to guard those cases rather than widen this table.
SENTENCE_TERMINATORS: dict[Script, frozenset[str]] = {
    Script.LATIN: frozenset({".", "!", "?"}),
    Script.ARABIC: frozenset({"۔", "؟", "!", ".", "?"}),
    Script.DEVANAGARI: frozenset({"।", "॥", "?", "!", "."}),
    Script.UNKNOWN: frozenset({".", "!", "?"}),
    Script.MIXED: frozenset({".", "!", "?", "۔", "।", "؟"}),
}

#: Clause-level breaks, used only when a single sentence exceeds the chunk
#: budget and must be split somewhere less natural.
CLAUSE_SEPARATORS: frozenset[str] = frozenset({",", "،", ";", ":", "—"})


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One unit of text handed to a model in a single forward pass."""

    text: str
    index: int
    #: True when the chunk ends at real sentence punctuation. False means it was
    #: split at a clause break or, worst case, a word boundary — worth logging,
    #: because it is where prosody artifacts at the join will show up.
    ends_on_sentence: bool


def normalize_whitespace(text: str) -> str:
    """
    Collapse runs of whitespace, strip ends, normalize newlines.

    Does NOT normalize Unicode: NFC/NFKC on Perso-Arabic can alter presentation
    forms the phonemizer depends on. If normalization is ever needed, it belongs
    per-runtime, not here.
    """
    raise NotImplementedError("Wave 2 / B3")


def split_sentences(text: str, script: Script) -> list[str]:
    """
    Split into sentences using the terminators for `script`.

    Preserves the terminator on the sentence it ends. Returns `[]` for empty or
    whitespace-only input rather than `[""]`, which would otherwise become a
    zero-length synthesis request.
    """
    raise NotImplementedError("Wave 2 / B3")


def chunk_for_synthesis(
    text: str,
    script: Script,
    *,
    max_chars: int,
    min_chars: int = 0,
) -> list[TextChunk]:
    """
    Pack text into chunks no longer than `max_chars`.

    Strategy, in order of preference:
      1. Whole sentences, greedily packed up to `max_chars`.
      2. A single over-long sentence is split at CLAUSE_SEPARATORS.
      3. Failing that, at word boundaries, with `ends_on_sentence=False`.

    `min_chars` avoids emitting a two-word trailing chunk, which these models
    render with noticeably wrong prosody — a short final chunk sounds clipped
    regardless of the model.

    `max_chars` is per-runtime and derives from the model's frame limit, so it is
    passed in rather than assumed. Never hardcode F5's value here; that is how
    engine-specific limits leak into shared code and silently truncate the
    others.
    """
    raise NotImplementedError("Wave 2 / B3")
