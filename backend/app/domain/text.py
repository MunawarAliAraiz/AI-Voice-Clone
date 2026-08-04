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
    return " ".join(text.split())


def _is_false_stop(text: str, i: int) -> bool:
    """
    True if the `.` at index `i` does not end a sentence.

    Only applies to Latin's `.`; `۔` and `।` have no such ambiguity, which is
    part of why the terminator table is per-script.

    Two cases: a decimal point (3.5) and a single-letter initial (J. Smith).
    Deliberately does not carry an abbreviation list — "Dr." and "etc." will
    over-split, which produces a slightly short chunk rather than a wrong one.
    """
    if text[i] != ".":
        return False
    nxt = text[i + 1] if i + 1 < len(text) else ""
    prv = text[i - 1] if i > 0 else ""
    if nxt.isdigit() and prv.isdigit():
        return True
    return bool(prv.isupper() and (i < 2 or not text[i - 2].isalpha()))


def split_sentences(text: str, script: Script) -> list[str]:
    """
    Split into sentences using the terminators for `script`.

    Preserves the terminator on the sentence it ends. Returns `[]` for empty or
    whitespace-only input rather than `[""]`, which would otherwise become a
    zero-length synthesis request.
    """
    text = normalize_whitespace(text)
    if not text:
        return []

    terminators = SENTENCE_TERMINATORS.get(script, SENTENCE_TERMINATORS[Script.LATIN])
    sentences: list[str] = []
    start = 0
    i = 0
    while i < len(text):
        if text[i] in terminators and not _is_false_stop(text, i):
            # Absorb a run of terminators, so "What?!" stays one sentence.
            end = i + 1
            while end < len(text) and text[end] in terminators:
                end += 1
            chunk = text[start:end].strip()
            if chunk:
                sentences.append(chunk)
            start = end
            i = end
            continue
        i += 1

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


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
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    sentences = split_sentences(text, script)
    if not sentences:
        return []

    # 1. Greedily pack whole sentences.
    packed: list[tuple[str, bool]] = []  # (text, ends_on_sentence)
    buf = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if buf:
                packed.append((buf, True))
                buf = ""
            packed.extend(_split_oversized(sentence, max_chars))
            continue
        candidate = f"{buf} {sentence}".strip() if buf else sentence
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            packed.append((buf, True))
            buf = sentence
    if buf:
        packed.append((buf, True))

    # 2. Merge a runt trailing chunk backwards. These models render a two-word
    #    final chunk with audibly wrong prosody — it sounds clipped regardless
    #    of the model — so a slightly long chunk beats a very short one.
    if min_chars > 0 and len(packed) >= 2 and len(packed[-1][0]) < min_chars:
        prev_text, _ = packed[-2]
        last_text, last_ends = packed[-1]
        merged = f"{prev_text} {last_text}"
        if len(merged) <= max_chars:
            packed[-2:] = [(merged, last_ends)]

    return [
        TextChunk(text=t, index=i, ends_on_sentence=ends)
        for i, (t, ends) in enumerate(packed)
    ]


def _split_oversized(sentence: str, max_chars: int) -> list[tuple[str, bool]]:
    """
    Break one over-long sentence, preferring clause breaks over word breaks.

    Everything produced here has `ends_on_sentence=False` — worth logging,
    because the joins between these chunks are exactly where prosody artifacts
    appear.
    """
    parts: list[str] = []
    buf = ""
    for token in _tokenize_clauses(sentence):
        candidate = f"{buf}{token}" if buf else token.lstrip()
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                parts.append(buf.strip())
            buf = token.lstrip()
    if buf.strip():
        parts.append(buf.strip())

    # A single clause may still exceed the budget; fall back to word boundaries.
    out: list[str] = []
    for part in parts:
        while len(part) > max_chars:
            cut = part.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars  # one unbroken token longer than the budget
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return [(p, False) for p in out]


def _tokenize_clauses(sentence: str) -> list[str]:
    """Split at clause separators, keeping each separator on its left part."""
    tokens: list[str] = []
    buf = ""
    for char in sentence:
        buf += char
        if char in CLAUSE_SEPARATORS:
            tokens.append(buf)
            buf = ""
    if buf:
        tokens.append(buf)
    return tokens
