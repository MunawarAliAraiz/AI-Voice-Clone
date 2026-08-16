"""
AI Voice Clone Studio — Urdu pronunciation normalization.

CONTRACT MODULE. Pure text transforms, no I/O, no torch — same constraint as
the rest of `domain/`.

WHY THIS EXISTS
----------------
The bake-off's arm-Eprod owner listen (docs/URDU_BAKEOFF_RESULTS.md SS5b-SS5d)
found and eval-verified (`eval/urdu_numerals.py`, `eval/run_number_fix_check.py`,
`eval/run_database_respell_v2.py`) that OmniVoice mispronounces raw digits and
two specific English loanwords, and that both are fixable by rewriting the
text BEFORE synthesis. Unlike `routing.TransformKind` (Perso-Arabic ->
Devanagari and friends), these rewrites need no model — a table lookup and a
couple of regex substitutions — so they run synchronously inside the pure
`routing.resolve()`, never through the impure `with_resolved_text()` seam.

WHAT IS NOT HERE
------------------
No general English-transliteration system. `office`/`check`/`GitHub`/`pull
request` already render correctly as plain Latin text and must stay
untouched — `DEFAULT_LOANWORD_LEXICON` holds exactly the words individually
tested and verified by ear, not a starting point for "respell every English
word". Extending the SHIPPED table requires the same per-word verify-by-ear
discipline as URL and database got, not a heuristic.

The user's own dictionary is a different matter and is deliberately not held
to that bar: they hear the defect, they choose the respelling, and they hear
the result. The discipline above exists because a maintainer is guessing on
behalf of users who cannot check; it does not apply to a user fixing their own
output. Which is why `apply_text_normalizations` takes the lexicon as an
argument rather than reading this module's table.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from functools import lru_cache
from types import MappingProxyType
from typing import Any

__all__ = [
    "DEFAULT_LOANWORD_LEXICON",
    "TextNormalization",
    "apply_text_normalizations",
    "effective_lexicon",
]


class TextNormalization(StrEnum):
    """One declared, per-spec pronunciation fix. See `ModelSpec.text_normalizations`."""

    #: Digit runs (ASCII or Eastern Arabic-Indic) -> Urdu cardinal words.
    NUMBERS = "numbers"
    #: The small, individually-verified English-loanword lexicon below.
    LOANWORD_LEXICON = "loanword_lexicon"


# ── Numbers: eval/urdu_numerals.py, ported verbatim ─────────────────────────
#
# 0-99, South Asian irregular cardinals. Not compositional (45 is not
# "four-ten five"; it is its own irregular word, "paintalees") -- same
# structure as Hindi, French 70-99, etc. Authored from general
# Urdu-language knowledge, not copied from a verified reference; a wrong
# word here is audibly wrong to any Urdu speaker, which is exactly the
# check this project already ran (docs/URDU_BAKEOFF_RESULTS.md SS5c/SS5d) —
# spot-check anything new the same way.
_ONES_TO_NINETY_NINE: dict[int, str] = {
    0: "صفر", 1: "ایک", 2: "دو", 3: "تین", 4: "چار", 5: "پانچ", 6: "چھ",
    7: "سات", 8: "آٹھ", 9: "نو", 10: "دس",
    11: "گیارہ", 12: "بارہ", 13: "تیرہ", 14: "چودہ", 15: "پندرہ",
    16: "سولہ", 17: "سترہ", 18: "اٹھارہ", 19: "انیس", 20: "بیس",
    21: "اکیس", 22: "بائیس", 23: "تیئس", 24: "چوبیس", 25: "پچیس",
    26: "چھبیس", 27: "ستائیس", 28: "اٹھائیس", 29: "انتیس", 30: "تیس",
    31: "اکتیس", 32: "بتیس", 33: "تینتیس", 34: "چونتیس", 35: "پینتیس",
    36: "چھتیس", 37: "سینتیس", 38: "اڑتیس", 39: "انتالیس", 40: "چالیس",
    41: "اکتالیس", 42: "بیالیس", 43: "تینتالیس", 44: "چوالیس", 45: "پینتالیس",
    46: "چھیالیس", 47: "سینتالیس", 48: "اڑتالیس", 49: "انچاس", 50: "پچاس",
    51: "اکاون", 52: "باون", 53: "ترپن", 54: "چون", 55: "پچپن",
    56: "چھپن", 57: "ستاون", 58: "اٹھاون", 59: "انسٹھ", 60: "ساٹھ",
    61: "اکسٹھ", 62: "باسٹھ", 63: "تریسٹھ", 64: "چونسٹھ", 65: "پینسٹھ",
    66: "چھیاسٹھ", 67: "سڑسٹھ", 68: "اڑسٹھ", 69: "انہتر", 70: "ستر",
    71: "اکہتر", 72: "بہتر", 73: "تہتر", 74: "چوہتر", 75: "پچہتر",
    76: "چھہتر", 77: "ستتر", 78: "اٹھہتر", 79: "اناسی", 80: "اسی",
    81: "اکاسی", 82: "بیاسی", 83: "تراسی", 84: "چوراسی", 85: "پچاسی",
    86: "چھیاسی", 87: "ستاسی", 88: "اٹھاسی", 89: "نواسی", 90: "نوے",
    91: "اکانوے", 92: "بانوے", 93: "ترانوے", 94: "چورانوے", 95: "پچانوے",
    96: "چھیانوے", 97: "ستانوے", 98: "اٹھانوے", 99: "ننانوے",
}

#: South Asian numbering (lakh/crore), not Western (million/billion).
_SCALES: tuple[tuple[int, str], ...] = (
    (10_000_000, "کروڑ"),
    (100_000, "لاکھ"),
    (1_000, "ہزار"),
    (100, "سو"),
)

#: Eastern Arabic-Indic digits -> ASCII.
_EASTERN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

#: A run of ASCII or Eastern Arabic-Indic digits.
_DIGIT_RUN = re.compile(r"[0-9۰-۹]+")


def _number_to_urdu_words(n: int) -> str:
    """
    Cardinal Urdu words for a non-negative integer, South Asian scale.

    2026 -> "دو ہزار چھبیس" (two thousand twenty-six) -- also the correct
    *year* reading with no special-casing: South Asian thousand-grouping
    already matches how years are read aloud (see the 14-vs-2026 asymmetry
    in docs/URDU_BAKEOFF_RESULTS.md SS3c).
    """
    if n < 0:
        raise ValueError(f"_number_to_urdu_words: negative input {n!r} not supported")
    if n < 100:
        return _ONES_TO_NINETY_NINE[n]

    for scale_value, scale_word in _SCALES:
        if n >= scale_value:
            count, remainder = divmod(n, scale_value)
            head = f"{_number_to_urdu_words(count)} {scale_word}"
            return head if remainder == 0 else f"{head} {_number_to_urdu_words(remainder)}"

    raise AssertionError(f"unreachable: {n} not < 100 but no scale matched")


def _expand_numbers(text: str) -> str:
    """Replace every digit run (ASCII or Eastern Arabic-Indic) with Urdu words."""

    def _replace(match: re.Match[str]) -> str:
        ascii_digits = match.group(0).translate(_EASTERN_DIGITS)
        return _number_to_urdu_words(int(ascii_digits))

    return _DIGIT_RUN.sub(_replace, text)


# ── Loanwords: exactly 2 verified entries, see module docstring ─────────────
#
# Both entries are now backed by BLIND REPEAT SAMPLING, not a single listen
# (docs/URDU_BAKEOFF_RESULTS.md SS9b/SS9c, `eval/run_loanword_reliability.py`).
# That method exists because synthesis is unseeded: `OmniVoiceBackend.synth()`
# sets no seed, so a loanword's pronunciation is a random variable and any n=1
# verdict is a coin flip. The owner demonstrated this by rating one
# byte-identical sentence wrong and then correct an hour apart.
#
# Measured, owner-rated, blind, over two rounds (round 1 n=4, round 2 n=8):
#
#     URL       verbatim      0/4     یو آر ایل   4/4
#     database  verbatim      0/4     ڈیٹا بےس   11/12
#                                     ڈیٹا base   7/12
#                                     ڈیٹا bays   4/12
#
# Both verbatim forms score ZERO, which is what justifies having entries here
# at all rather than leaving the text alone.
#
# `ڈیٹا بےس` uses bari ye (U+06D2) for the /eɪ/. The earlier all-Urdu attempt
# `ڈیٹا بیس` was rejected because بیس is also the Urdu word for "twenty"; bari
# ye avoids that reading. `ڈیٹا base` -- which shipped until 2026-08-16 and was
# recorded as "verified" on one listen -- is a genuine coin flip at 7/12: Latin
# `base` standing alone after Urdu text is often read as "boss". Note it is
# only correct as the TAIL of the Latin token `database`, so the model reads a
# Latin token as a whole rather than letter by letter.
#
# `ڈیٹا bays` is kept here as a warning: it produced the single best-sounding
# clip of the whole experiment ("most accurate for a native Urdu speaker") and
# still scored 4/12. Picking a spelling by its best clip would have shipped the
# second-worst one.
# `میٹنگ` is the FIRST Perso-Arabic key, added 2026-08-16 after A3 passed. It
# is also the first entry whose defect the corpus GOLD shares -- gold spells it
# میٹنگ too, so this is not a transliteration artefact and no choice of model
# would have removed it.
#
# Blind, n=4 per spelling, one carrier sentence (`eval/run_meeting_respell.py`,
# owner-rated):
#
#     میٹنگ   (in use)   2/4    <- the defect, and INTERMITTENT
#     می ٹنگ  (split)    3/4
#     meeting (Latin)    4/4
#     مِیٹنگ             4/4
#     میٹِنگ             4/4
#     مِیٹِنگ            4/4    <- shipped
#     میٹینگ             4/4
#     مِٹنگ              4/4
#
# Two things this measured that no amount of reading the text could have:
# respelling a word that is ALREADY Perso-Arabic does change how OmniVoice
# reads it (the whole premise of an either-script dictionary), and the broken
# spelling is right half the time rather than never -- which is why it survived
# every previous review and surfaced only on one listen.
#
# Five candidates tie at 4/4 and n=4 cannot separate them. `مِیٹِنگ` is chosen
# on a non-acoustic tie-break: it adds only DIACRITICS to the standard
# skeleton, so the text a user sees in the Composer still reads as میٹنگ. The
# alternatives change letters or word boundaries and look wrong on the page.
# Deliberately NOT resolved with another sampling round -- that is a value the
# user's own dictionary lets them override by ear, and picking it for them is
# the work the dictionary exists to avoid.
DEFAULT_LOANWORD_LEXICON: Mapping[str, str] = MappingProxyType(
    {
        "URL": "یو آر ایل",
        "database": "ڈیٹا بےس",
        "میٹنگ": "مِیٹِنگ",
    }
)


# ── Keys may be in EITHER script ────────────────────────────────────────────
#
# Until 2026-08-16 every key here was Latin, because every measured defect was
# an English loanword sitting in Latin inside Urdu text. A3's passing run
# produced the counter-example: میٹنگ is read as "mating", and the text arrives
# ALREADY in Perso-Arabic -- the transliterator converted it, and the corpus
# gold spells it that way too. A Latin-keyed table cannot reach that word at
# all, so keys are matched in whatever script they are written in.
#
# `\b` is Unicode-aware in Python's `re`: Perso-Arabic letters are `\w`, so a
# word boundary falls between میٹنگ and a space or an Urdu full stop exactly as
# it does for Latin. No separate Arabic code path is needed.
#
# Matching is CASE-INSENSITIVE, which is a deliberate change from the
# hardcoded table's behaviour. A user typing a dictionary entry will write
# `Database` once and then type `database` in the Composer; a table that
# silently missed the second is a support burden with no upside. The
# REPLACEMENT is used verbatim -- case is not carried over, because the
# replacement is usually Perso-Arabic, which has no case to carry.
#
# Longest key first, so `pull request` wins over a hypothetical `request`.
# Python's alternation is first-match-wins, not longest-match-wins, and the
# dict's insertion order is the user's, which is no order at all.
@lru_cache(maxsize=64)
def _compiled(entries: tuple[tuple[str, str], ...]) -> tuple[re.Pattern[str], Mapping[str, str]]:
    by_key = {key.casefold(): replacement for key, replacement in entries}
    pattern = re.compile(
        "|".join(
            rf"\b{re.escape(key)}\b"
            for key in sorted((k for k, _ in entries), key=len, reverse=True)
        ),
        re.IGNORECASE,
    )
    return pattern, by_key


def _respell_loanwords(text: str, lexicon: Mapping[str, str]) -> str:
    """Word-boundary substitution for the supplied lexicon only."""
    if not lexicon:
        return text
    pattern, by_key = _compiled(tuple(lexicon.items()))
    return pattern.sub(lambda m: by_key[m.group(0).casefold()], text)


_APPLIERS: dict[TextNormalization, Callable[[str, Mapping[str, str]], str]] = {
    TextNormalization.NUMBERS: lambda text, _lexicon: _expand_numbers(text),
    TextNormalization.LOANWORD_LEXICON: _respell_loanwords,
}


def effective_lexicon(
    entries: Iterable[Mapping[str, Any]],
    defaults: Mapping[str, str] = DEFAULT_LOANWORD_LEXICON,
) -> dict[str, str]:
    """
    Merge a user's dictionary rows over the shipped defaults.

    `entries` are read by SUBSCRIPT (`entry["key_text"]`), which is what an
    `aiosqlite.Row` and a plain dict both support — so `domain/` keeps not
    importing `db/` and the tests need no database.

    Three behaviours, in this order of precedence:

    1. A user entry with a NEW key adds it.
    2. A user entry whose key matches a default REPLACES it. The user has heard
       both their own text and the shipped spelling; the maintainer has heard
       neither.
    3. A **disabled** user entry whose key matches a default SUPPRESSES it.
       This is the only way to switch off a built-in entry, and it is why
       disabled rows cannot simply be filtered out before getting here.

    Matching is case-insensitive to mirror the matcher, so a user entry for
    `Database` overrides the shipped `database` rather than sitting beside it
    and losing a coin flip on alternation order.

    Pure, and takes rows rather than fetching them — same reasoning as
    `apply_text_normalizations`. Golden rule 4.
    """
    folded_defaults = {key.casefold(): (key, value) for key, value in defaults.items()}
    merged: dict[str, str] = dict(defaults)

    for entry in entries:
        key = str(entry["key_text"])
        shadowed = folded_defaults.get(key.casefold())
        if shadowed is not None:
            merged.pop(shadowed[0], None)
        if entry["is_enabled"]:
            merged[key] = str(entry["replacement"])

    return merged


def apply_text_normalizations(
    text: str,
    kinds: tuple[TextNormalization, ...],
    lexicon: Mapping[str, str] = DEFAULT_LOANWORD_LEXICON,
) -> tuple[str, tuple[TextNormalization, ...]]:
    """
    Apply each declared normalization, in the order given.

    Returns the resulting text plus exactly the kinds that changed something —
    a spec can declare `LOANWORD_LEXICON` and still see `()` returned for it on
    a particular input with no matching word, so callers reflect reality
    (`RoutePlan.text_normalizations`), not mere capability.

    `lexicon` is a PARAMETER rather than a module global so the user-editable
    dictionary can be passed in without this module — or `routing.resolve()`,
    which calls it — ever touching the database. Golden rule 4 says routing is
    pure; a pure function is allowed to receive data, it is just not allowed to
    go and fetch it. The caller loads the rows; this applies them.
    """
    applied: list[TextNormalization] = []
    for kind in kinds:
        new_text = _APPLIERS[kind](text, lexicon)
        if new_text != text:
            applied.append(kind)
        text = new_text
    return text, tuple(applied)
