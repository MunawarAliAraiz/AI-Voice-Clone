"""
Digit -> Urdu-number-word expansion. EVAL-ONLY, same rule as urdu_represent.py:
nothing here may be imported by `backend/app/` until a bake-off shows it helps.

WHY THIS EXISTS
----------------
The arm-Eprod owner listen (docs/URDU_BAKEOFF_RESULTS.md §5b) found OmniVoice's
one systematic, reproducible failure: bare digits. `num_ascii` (3, 45) and
`num_eastern` (same digits, Eastern Arabic-Indic glyphs) both failed, and the
digit *script* was not the variable -- the fact that they were digits at all
was. `date` (14, 2026) failed too, on both numbers, not just one. This is not
a new observation: the bake-off corpus's own free-text listener comments
(§3c) already said it -- "in urdu we say 'chouda' instead of fourteen", "45
is called 'pentalees'". Feeding a TTS model the *word* a number is actually
spoken as, instead of the digit glyph, is a standard TTS-frontend technique
(every production English TTS system expands "2026" before synthesis too);
nothing here is speculative about the general approach, only about the exact
Urdu spellings, which is why the fix is verified by ear, not assumed correct.

WHAT THIS DOES NOT DO
----------------------
It does not touch Latin islands ("GitHub", "URL") or non-numeric text at all.
Those are a separate, already-documented failure mode (§5b: "URL" -> "oo r
l", "database" with an Arabic-accented T) with a different likely fix
(explicit English-reading hints), out of scope here.

ACCURACY CAVEAT
----------------
Urdu numbers 1-99 are NOT compositional the way English's are (45 is not
"four-ten five"; it is its own irregular word, "paintalees") -- same
structure as Hindi, French 70-99, etc. The table below was authored from
general Urdu-language knowledge, not copied from a verified reference, and
this project's own rule for exactly this situation
(docs/URDU_MODEL_LICENSING.md's repeated "verify, don't assume" thread) says
not to trust it blind. The corpus's actual failing numbers (3, 14, 45, 2026)
are the high-confidence common ones; anything else this produces should be
spot-checked by ear the same way -- a wrong word here is audibly wrong to any
Urdu speaker, which is exactly the check this project already runs.
"""

from __future__ import annotations

import re

__all__ = ["number_to_urdu_words", "expand_numbers_in_text"]

#: 0-99, South Asian irregular cardinals. Not compositional -- see module
#: docstring. Perso-Arabic (Urdu) script.
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

#: South Asian numbering (lakh/crore), not Western (million/billion) -- the
#: register this corpus's category is conversational Urdu, which uses lakh/
#: crore even when writing Arabic numerals.
_SCALES: tuple[tuple[int, str], ...] = (
    (10_000_000, "کروڑ"),
    (100_000, "لاکھ"),
    (1_000, "ہزار"),
    (100, "سو"),
)

#: Eastern Arabic-Indic digits -> ASCII, same table as urdu_represent.py's
#: to_ascii_digits (not imported, to keep this module dependency-free and
#: usable standalone).
_EASTERN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

#: Matches a run of ASCII or Eastern Arabic-Indic digits.
_DIGIT_RUN = re.compile(r"[0-9۰-۹]+")


def number_to_urdu_words(n: int) -> str:
    """
    Cardinal Urdu words for a non-negative integer, South Asian scale.

    100 -> "ایک سو" (one hundred), not bare "سو" -- Urdu says "ek sau", not
    just "sau", the same way English needs "one hundred" not "hundred".
    2026 -> "دو ہزار چھبیس" (two thousand twenty-six) -- this is also the
    correct *year* reading with no special-casing needed; South Asian
    thousand-grouping already matches how years are conventionally read
    aloud (see the already-documented 14-vs-2026 asymmetry in
    docs/URDU_BAKEOFF_RESULTS.md §3c).
    """
    if n < 0:
        raise ValueError(f"number_to_urdu_words: negative input {n!r} not supported")
    if n < 100:
        return _ONES_TO_NINETY_NINE[n]

    for scale_value, scale_word in _SCALES:
        if n >= scale_value:
            count, remainder = divmod(n, scale_value)
            head = f"{number_to_urdu_words(count)} {scale_word}"
            return head if remainder == 0 else f"{head} {number_to_urdu_words(remainder)}"

    raise AssertionError(f"unreachable: {n} not < 100 but no scale matched")


def expand_numbers_in_text(text: str) -> str:
    """
    Replace every digit run (ASCII or Eastern Arabic-Indic) in `text` with its
    Urdu cardinal-word expansion. Everything else -- Urdu words, Latin
    islands, punctuation -- is left untouched.
    """

    def _replace(match: re.Match[str]) -> str:
        ascii_digits = match.group(0).translate(_EASTERN_DIGITS)
        return number_to_urdu_words(int(ascii_digits))

    return _DIGIT_RUN.sub(_replace, text)


if __name__ == "__main__":  # quick eyeball: python eval/urdu_numerals.py
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    checks = [3, 14, 19, 26, 45, 100, 2026]
    for value in checks:
        print(f"{value:>6} -> {number_to_urdu_words(value)}")

    print()
    samples = [
        "میٹنگ 3 بجے شروع ہوگی اور تقریباً 45 منٹ چلے گی۔",
        "میٹنگ ۳ بجے شروع ہوگی اور تقریباً ۴۵ منٹ چلے گی۔",
        "یہ رپورٹ 14 اگست 2026 تک جمع کرانی ہے۔",
    ]
    for s in samples:
        print(f"  in : {s}")
        print(f"  out: {expand_numbers_in_text(s)}")
        print()
