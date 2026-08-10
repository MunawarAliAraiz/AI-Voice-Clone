"""
AI Voice Clone Studio — Heuristic Speech-Direction analyzer.

CONTRACT MODULE (signature frozen). Phase 2. PURE — stdlib only, no I/O, no
clock, no inference imports.

Turns raw text into a `DirectionPlan` (see `direction.py`) using only signals a
zero-dependency, offline heuristic can read:

  * **Segmentation** — reuse `domain.text.split_sentences` with the detected
    script's terminators (Urdu ۔, Devanagari । included). One sentence →
    one `DirectedSegment`.
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

── Implementation notes (this body) ─────────────────────────────────────────

Emotion is decided by a fixed-priority lexicon scan (ANGRY, HAPPY, SAD,
ANXIOUS, EXCITED, CALM, SERIOUS — first match wins, so "angry" always beats a
trailing "!" turning into EXCITED) falling back to punctuation only when no
lexicon word matched: `?` → QUESTIONING, `!` → EXCITED, trailing `...`/`—` →
CALM, else NEUTRAL. QUESTIONING has no keyword lexicon on purpose —
interrogative words like "kyun"/"kaise" appear too often in non-questions to
be a reliable signal, so `?` alone carries that signal.

`tone` is NOT analyzer-derived. Every segment gets `Tone.NEUTRAL` by
dataclass default (see `direction.py`), including the narrator/commentary
`Tone.NARRATIVE` value — the taxonomy exists so a future renderer can honor it
once some model actually takes tone conditioning, but no detection heuristic
is built here. Inventing a shaky one just to populate the enum would be worse
than leaving it honestly unset.

Intensity and energy are small integer scores built from the same raw
signals (exclamation count, an ALL-CAPS run, an intensifier word, a hedge
word) — energy additionally weights EXCITED/ANGRY up and CALM/SAD/ANXIOUS
down, since "energy" is meant to also track the emotion itself, not just
punctuation. Score <= -1 → LOW, score == 0 → MEDIUM, score >= 1 → HIGH.

Emphasis spans come from two sources: an ALL-CAPS run of >= 2 Latin letters
(one run per word — "STOP NOW" yields two spans, not one merged span) and
``*asterisk*`` pairs, whose span EXCLUDES the asterisks themselves (so a
renderer emphasizing the substring never has to know the markup existed). A
terminal `!` is treated purely as an emotion/intensity signal, not as a third
emphasis source: a span over the whole trailing clause would, in the common
case, just duplicate the entire segment.

`summary`'s dominant value per field is the most-common value across
segments, ties broken by first-seen order (the value that appeared earliest
among those tied for the max count wins) — deterministic without needing enum
ordering. Emotion dominance additionally ignores NEUTRAL segments unless
every segment is NEUTRAL, per the "most common non-NEUTRAL emotion if any,
else NEUTRAL" rule.
"""

from __future__ import annotations

import re

from .direction import (
    DEFAULT_PAUSE_MS,
    DirectedSegment,
    DirectionPlan,
    DirectionSummary,
    Emotion,
    EmphasisSpan,
    Level,
    Rate,
)
from .language import detect_script
from .text import split_sentences

__all__ = ["analyze"]

# ── Lexicons ─────────────────────────────────────────────────────────────────
#
# Small but real: a handful of unambiguous words per emotion per script/
# language, covering Urdu (Roman Latin + Perso-Arabic), Hindi (Devanagari +
# Roman), and English. Matching is whole-word (`\b...\b`) and case-insensitive
# — case-insensitivity is a no-op for Arabic/Devanagari, which have no case,
# so one flag covers all three scripts.

EMOTION_KEYWORDS: dict[Emotion, tuple[str, ...]] = {
    Emotion.ANGRY: (
        "angry", "furious", "mad", "gussa", "ghussa", "غصہ", "गुस्सा", "क्रोध",
    ),
    Emotion.HAPPY: (
        "happy", "glad", "joyful", "wonderful", "delighted",
        "khush", "khushi", "خوش", "खुश", "खुशी",
    ),
    Emotion.SAD: (
        "sad", "unhappy", "heartbroken", "sorrowful",
        "udaas", "dukhi", "اداس", "غمگین", "उदास", "दुखी",
    ),
    # Starting list only — not verified against native speakers, same caveat
    # the rest of this lexicon carries. "dar"/"डर" (fear) is short; worth a
    # spot-check for false-positive matches inside unrelated longer words.
    # "चिंतित" (adjective form), not "चिंता" (noun form): the noun ends in the
    # combining vowel sign ा (Unicode category Mc), which Python's `\w` does
    # not treat as a word character, so a `\b...\b` pattern never matches at
    # its right edge — confirmed empirically, not a hypothetical. Any future
    # Devanagari/Perso-Arabic keyword ending in a bare combining mark/matra
    # needs the same check before being added.
    Emotion.ANXIOUS: (
        "worried", "anxious", "nervous", "scared", "afraid",
        "pareshan", "fikar", "dar",
        "پریشان", "فکر", "ڈر",
        "परेशान", "चिंतित", "डर",
    ),
    Emotion.EXCITED: (
        "excited", "amazing", "awesome", "thrilled",
        "josh", "shandar", "پرجوش", "उत्साहित", "जोश",
    ),
    Emotion.CALM: (
        "calm", "relaxed", "peaceful", "soothing",
        "sukoon", "aaram", "پرسکون", "سکون", "शांत", "सुकून",
    ),
    Emotion.SERIOUS: (
        "serious", "important", "warning", "urgent",
        "zaroori", "sanjeeda", "سنجیدہ", "ضروری", "गंभीर", "जरूरी",
    ),
}

#: Fixed scan order. First match wins, which is what makes "!" turning into
#: EXCITED-unless-angry deterministic: ANGRY is checked before EXCITED ever
#: gets a chance to be inferred from punctuation. ANXIOUS sits between SAD and
#: EXCITED: SAD first because SAD/ANXIOUS lexicons are the most likely pair to
#: collide (both low-arousal negative affect) and SAD's tests must not shift;
#: ANXIOUS ahead of EXCITED because anxious text can carry a "!" that would
#: otherwise fall through to the punctuation-only EXCITED branch, mirroring
#: the ANGRY-before-EXCITED precedent above.
_EMOTION_PRIORITY: tuple[Emotion, ...] = (
    Emotion.ANGRY,
    Emotion.HAPPY,
    Emotion.SAD,
    Emotion.ANXIOUS,
    Emotion.EXCITED,
    Emotion.CALM,
    Emotion.SERIOUS,
)

INTENSIFIERS: tuple[str, ...] = (
    "very", "really", "so", "extremely",
    "bohat", "boht", "bahut", "بہت", "बहुत",
)

HEDGES: tuple[str, ...] = (
    "maybe", "perhaps", "probably", "somewhat",
    "shayad", "thoda", "kuch", "شاید", "شاید سا", "शायद", "थोड़ा",
)

#: Precompiled once, keyed by word, so lexicon scans don't recompile a regex
#: per call. `\b` is Unicode-aware in Python's `re`, so it works unmodified
#: across Latin, Perso-Arabic and Devanagari.
_WORD_PATTERNS: dict[str, re.Pattern[str]] = {
    word: re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
    for words in (*EMOTION_KEYWORDS.values(), INTENSIFIERS, HEDGES)
    for word in words
}


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(_WORD_PATTERNS[word].search(text) for word in words)


# ── Punctuation ──────────────────────────────────────────────────────────────

#: Every sentence-final mark across the three scripts, plus ellipsis/dash.
_TERMINATOR_CHARS = frozenset(".!?۔؟،।॥…—")

#: Clause-level separators, used only to judge how clause-dense a sentence is
#: for the rate heuristic (not used for splitting — that's `text.py`'s job).
_CLAUSE_SEP_CHARS = frozenset(",،;؛")

_CAPS_RUN_RE = re.compile(r"\b[A-Z]{2,}\b")
_ASTERISK_RE = re.compile(r"\*([^*\n]+)\*")


def _trailing_punct(text: str) -> str:
    """The run of terminator characters at the end of `text`, or ''."""
    stripped = text.rstrip()
    i = len(stripped)
    while i > 0 and stripped[i - 1] in _TERMINATOR_CHARS:
        i -= 1
    return stripped[i:]


def _has_allcaps_run(text: str) -> bool:
    return _CAPS_RUN_RE.search(text) is not None


def _count_clause_seps(text: str) -> int:
    return sum(1 for ch in text if ch in _CLAUSE_SEP_CHARS)


# ── Per-field inference ──────────────────────────────────────────────────────


def _determine_emotion(text: str, terminal: str) -> Emotion:
    for emotion in _EMOTION_PRIORITY:
        if _contains_any(text, EMOTION_KEYWORDS[emotion]):
            return emotion
    if "?" in terminal or "؟" in terminal:
        return Emotion.QUESTIONING
    if "!" in terminal:
        return Emotion.EXCITED
    if terminal in ("...", "..", "…") or terminal.endswith("—"):
        return Emotion.CALM
    return Emotion.NEUTRAL


def _level_from_score(score: int) -> Level:
    if score <= -1:
        return Level.LOW
    if score == 0:
        return Level.MEDIUM
    return Level.HIGH


def _determine_intensity(text: str, has_intensifier: bool, has_hedge: bool) -> Level:
    score = 0
    exclamations = text.count("!")
    if exclamations >= 1:
        score += 1
    if exclamations >= 2:
        score += 1
    if _has_allcaps_run(text):
        score += 1
    if has_intensifier:
        score += 1
    if has_hedge:
        score -= 1
    return _level_from_score(score)


def _determine_energy(
    text: str, emotion: Emotion, has_intensifier: bool, has_hedge: bool
) -> Level:
    score = 0
    if text.count("!") >= 1:
        score += 1
    if _has_allcaps_run(text):
        score += 1
    if emotion in (Emotion.EXCITED, Emotion.ANGRY):
        score += 1
    if emotion in (Emotion.CALM, Emotion.SAD, Emotion.ANXIOUS):
        score -= 1
    if has_intensifier:
        score += 1
    if has_hedge:
        score -= 1
    return _level_from_score(score)


#: Sentences longer than this, or with this many clause separators, are
#: judged clause-dense enough to slow down for.
_SLOW_LEN_THRESHOLD = 100
_SLOW_CLAUSE_THRESHOLD = 3
#: Short AND exclamatory reads as a quick, punchy delivery.
_FAST_LEN_THRESHOLD = 40


def _determine_rate(text: str, terminal: str) -> Rate:
    if len(text) > _SLOW_LEN_THRESHOLD or _count_clause_seps(text) >= _SLOW_CLAUSE_THRESHOLD:
        return Rate.SLOW
    if "!" in terminal and len(text) <= _FAST_LEN_THRESHOLD:
        return Rate.FAST
    return Rate.NORMAL


def _emphasis_spans(text: str) -> tuple[EmphasisSpan, ...]:
    spans: list[EmphasisSpan] = []
    excluded: list[tuple[int, int]] = []

    for match in _ASTERISK_RE.finditer(text):
        # Span covers the inner text only — the asterisks are markup, not
        # content a renderer should ever have to strip itself.
        spans.append(EmphasisSpan(match.start(1), match.end(1)))
        excluded.append((match.start(), match.end()))

    for match in _CAPS_RUN_RE.finditer(text):
        start, end = match.start(), match.end()
        if any(start >= ex_start and end <= ex_end for ex_start, ex_end in excluded):
            continue  # already covered by an *asterisk* span (e.g. "*NEVER*")
        spans.append(EmphasisSpan(start, end))

    spans.sort(key=lambda span: span.start)
    return tuple(spans)


def _pause_after_ms(terminal: str) -> int:
    if terminal in ("...", "..", "…") or terminal.endswith("—"):
        return DEFAULT_PAUSE_MS + 160
    if any(ch in terminal for ch in "?!؟"):
        return DEFAULT_PAUSE_MS + 60
    return DEFAULT_PAUSE_MS


# ── Summary dominance ────────────────────────────────────────────────────────


def _mode_first_seen[T](values: list[T]) -> T:
    """Most frequent value; ties go to whichever tied value appeared first."""
    counts: dict[T, int] = {}
    order: list[T] = []
    for value in values:
        if value not in counts:
            counts[value] = 0
            order.append(value)
        counts[value] += 1
    return max(order, key=lambda value: counts[value])


def _dominant_emotion(segments: tuple[DirectedSegment, ...]) -> Emotion:
    non_neutral = [s.emotion for s in segments if s.emotion is not Emotion.NEUTRAL]
    if not non_neutral:
        return Emotion.NEUTRAL
    return _mode_first_seen(non_neutral)


# ── Entry point ──────────────────────────────────────────────────────────────


def analyze(text: str, language: str) -> DirectionPlan:
    """
    Analyze `text` (user-declared `language`) into a model-agnostic
    `DirectionPlan`. Pure and deterministic: same input → same output.

    Empty / whitespace-only input returns an empty plan (no segments), never a
    zero-length segment.
    """
    script, _ratios = detect_script(text)

    if not text.strip():
        return DirectionPlan(
            language=language,
            source_script=script.value,
            segments=(),
            summary=DirectionSummary(),
        )

    sentences = split_sentences(text, script)

    segments: list[DirectedSegment] = []
    for index, sentence in enumerate(sentences):
        terminal = _trailing_punct(sentence)
        emotion = _determine_emotion(sentence, terminal)
        has_intensifier = _contains_any(sentence, INTENSIFIERS)
        has_hedge = _contains_any(sentence, HEDGES)

        segments.append(
            DirectedSegment(
                text=sentence,
                index=index,
                emotion=emotion,
                intensity=_determine_intensity(sentence, has_intensifier, has_hedge),
                energy=_determine_energy(sentence, emotion, has_intensifier, has_hedge),
                rate=_determine_rate(sentence, terminal),
                emphasis=_emphasis_spans(sentence),
                pause_after_ms=_pause_after_ms(terminal),
            )
        )

    segments_t = tuple(segments)
    summary = DirectionSummary(
        emotion=_dominant_emotion(segments_t),
        intensity=_mode_first_seen([s.intensity for s in segments_t]),
        rate=_mode_first_seen([s.rate for s in segments_t]),
    )

    return DirectionPlan(
        language=language,
        source_script=script.value,
        segments=segments_t,
        summary=summary,
    )
