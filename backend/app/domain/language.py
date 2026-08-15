"""
AI Voice Clone Studio — Language and script primitives.

CONTRACT MODULE. Wave 0. Implementations marked NotImplementedError are B3's.

Pure. No I/O, no torch, no model state. Everything here is a value object or a
total function over text.

The central rule, which the API and UI both depend on:

    The user DECLARES the language. The code DETECTS the script.

Script detection cannot distinguish Roman Urdu from English — "Aap kaise hain"
and "How are you" are both Latin. Do not build a classifier for this; it will be
wrong on short inputs forever. Instead, `(language="ur", script=LATIN)` *is* the
definition of Roman Urdu, unambiguously, because the user said so.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Script",
    "LanguageCode",
    "TextProfile",
    "detect_script",
    "profile_text",
]


class Script(StrEnum):
    """Writing system, detected from the text itself."""

    LATIN = "latin"
    #: Perso-Arabic, as used for Urdu.
    ARABIC = "arabic"
    DEVANAGARI = "devanagari"
    #: Text with no strongly-scripted characters (digits, punctuation only).
    UNKNOWN = "unknown"
    #: Meaningful amounts of more than one script. Callers must decide policy;
    #: routing rejects it rather than guessing.
    MIXED = "mixed"


class LanguageCode(StrEnum):
    """
    Languages the product exposes.

    Deliberately small. A language appears here only when at least one spec has
    a Phase A-verified cell for it. Adding a member without a verified spec
    produces a catalog that promises what it cannot deliver.
    """

    URDU = "ur"
    ENGLISH = "en"


#: Unicode ranges by script. Ported from translation_service.py:39-61, which had
#: these correct; it was the only reusable thing in that module.
SCRIPT_RANGES: dict[Script, tuple[tuple[int, int], ...]] = {
    Script.ARABIC: (
        (0x0600, 0x06FF),  # Arabic
        (0x0750, 0x077F),  # Arabic Supplement
        (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
        (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
    ),
    Script.DEVANAGARI: (
        (0x0900, 0x097F),
        (0xA8E0, 0xA8FF),  # Devanagari Extended
    ),
    Script.LATIN: (
        (0x0041, 0x005A),
        (0x0061, 0x007A),
        (0x00C0, 0x024F),  # Latin-1 Supplement + Extended-A/B
    ),
}

#: Fraction of scripted characters a single script must hold to win outright.
#: Below this with two contenders present, the result is MIXED.
SCRIPT_DOMINANCE_THRESHOLD = 0.85

#: The script a declared language is natively written in. Latin runs inside text
#: declared as one of these are code-switching — loanwords, technical terms,
#: proper nouns ("GitHub", "pull request", "office") — not evidence that the
#: input is ambiguous. Only languages with a non-Latin native script appear.
NATIVE_SCRIPTS: dict[str, Script] = {
    LanguageCode.URDU.value: Script.ARABIC,
}

#: How much Latin a declared-native text may carry before it stops being
#: "native with English islands" and becomes an English sentence with a token of
#: the native script in it. Past this the result stays MIXED and the caller gets
#: an explicit refusal carrying the ratios, rather than a guess.
#:
#: Deliberately generous, because heavy code-switching is not an edge case in
#: Pakistani Urdu — UrduSpeech is 57% code-switched by design. "میں نے GitHub پر
#: pull request create کر دی ہے" is 64% Latin and is still ordinary Urdu; only
#: past ~3/4 does the native script stop being the sentence's frame.
LATIN_ISLAND_CEILING = 0.75


@dataclass(frozen=True, slots=True)
class TextProfile:
    """
    What the system knows about a piece of input text.

    `language` is what the *user declared* — never inferred. `script` is
    measured. Routing consumes exactly this plus the catalog.
    """

    text: str
    language: str
    script: Script
    #: Per-script share of scripted characters. Diagnostics, and the source of
    #: the "this looks like Devanagari, but you selected Urdu" UI warning.
    script_ratios: dict[Script, float]
    char_count: int

    @property
    def is_roman_urdu(self) -> bool:
        """Urdu declared, Latin script measured. A first-class supported case."""
        return self.language == LanguageCode.URDU.value and self.script is Script.LATIN

    @property
    def is_rtl(self) -> bool:
        """
        Whether the UI should render this right-to-left.

        Keys off the *detected script*, never `language == 'ur'` — the latter
        wrongly right-aligns Roman Urdu.
        """
        return self.script is Script.ARABIC


def _script_of(char: str) -> Script | None:
    """The script a single character belongs to, or None if unscripted."""
    cp = ord(char)
    for script, ranges in SCRIPT_RANGES.items():
        if any(lo <= cp <= hi for lo, hi in ranges):
            return script
    return None


def detect_script(text: str) -> tuple[Script, dict[Script, float]]:
    """
    Measure the dominant script of `text`.

    Counts only strongly-scripted characters: digits, punctuation, whitespace,
    and emoji are ignored, so "Hello! 123" is LATIN rather than MIXED.

    Returns:
        (dominant, ratios). `dominant` is UNKNOWN when no scripted characters
        are present, and MIXED when no single script reaches
        SCRIPT_DOMINANCE_THRESHOLD.
    """
    counts: dict[Script, int] = {}
    total = 0
    for char in text:
        script = _script_of(char)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
            total += 1

    if total == 0:
        return Script.UNKNOWN, {}

    ratios = {script: n / total for script, n in counts.items()}
    dominant, share = max(ratios.items(), key=lambda kv: kv[1])
    if share >= SCRIPT_DOMINANCE_THRESHOLD:
        return dominant, ratios
    return Script.MIXED, ratios


def _resolve_latin_islands(
    script: Script, ratios: dict[Script, float], language: str
) -> Script:
    """
    Rescue code-switched native text that `detect_script` called MIXED.

    `detect_script` is deliberately language-agnostic — `direction_analyze`
    calls it to choose a sentence-terminator set, where MIXED yielding the union
    of all terminators is the right answer. But it has no way to know that a
    single Latin word inside Urdu is normal. "میں نے GitHub پر ایک نیا پل
    ریکویسٹ بھیجا ہے" measures 82.9% Arabic, lands under
    SCRIPT_DOMINANCE_THRESHOLD, and so was rejected as ambiguous — despite
    code-switching being the single most common shape of real Pakistani Urdu,
    and despite the models handling it well (the bake-off scored it 5/5).

    So here, where the declared language IS known, dominance is re-measured over
    the language-bearing scripts alone, with Latin discounted as islands. The
    text must still be overwhelmingly its native script among the scripts that
    actually carry the language — a genuine Urdu/Hindi mix stays MIXED, because
    that is real ambiguity rather than code-switching.
    """
    if script is not Script.MIXED:
        return script
    native = NATIVE_SCRIPTS.get(language)
    if native is None:
        return script

    if ratios.get(Script.LATIN, 0.0) >= LATIN_ISLAND_CEILING:
        return script

    carrying = sum(share for s, share in ratios.items() if s is not Script.LATIN)
    if carrying <= 0.0:
        return script
    if ratios.get(native, 0.0) / carrying >= SCRIPT_DOMINANCE_THRESHOLD:
        return native
    return script


def profile_text(text: str, language: str) -> TextProfile:
    """
    Build a TextProfile from user-declared `language` and measured script.

    Does not validate that the pair is routable — that is `routing.resolve`'s
    job, and it needs the profile in order to explain the rejection.

    `script_ratios` always carries the TRUE measured shares, even when
    `_resolve_latin_islands` resolves a MIXED reading to a native script — so
    nothing downstream loses the information that English islands were present.
    """
    script, ratios = detect_script(text)
    script = _resolve_latin_islands(script, ratios, language)
    return TextProfile(
        text=text,
        language=language,
        script=script,
        script_ratios=ratios,
        char_count=len(text),
    )
