"""
Urdu text representations for the bake-off. EVAL-ONLY — not production.

Nothing here may be imported by `backend/app/`. It is deliberately outside the
app so that no half-tested transform can leak into the generate path: per the
plan, a production `domain/urdu_text.py` is built only if the bake-off shows a
representation actually helps.

What this module does and does not do
-------------------------------------
It does NOT transliterate Perso-Arabic to Devanagari. That conversion is hard
(abjad -> abugida; Perso-Arabic omits the short vowels Devanagari must write,
so it needs vowel restoration), and a mediocre implementation would confound
the experiment: a bad arm-C/I/J result could mean "the model cannot speak Urdu
from Devanagari" OR "the transliterator is bad", with no way to tell which.

So the Devanagari is hand-authored gold in `fixtures/urdu_corpus.json`, and
arms C/I/J are a CEILING TEST — an upper bound on what any converter could
deliver. This module only performs the small, mechanical, fully-reversible
operations that are genuinely safe to automate:

  * `strip_nuqta`     — derives arm I (plain) from arm J (phoneme-preserving)
  * `normalize_urdu`  — rung B of the representation ladder, on its own toggle
  * `to_ascii_digits` — a normalization step held separately, because it is the
                        one most likely to HURT (see its docstring)

Latin islands ("GitHub", "pull request") are never touched by anything here.
Whether a model copes with them is what the code-switching criterion measures;
pre-solving it would hide the answer.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Representation",
    "CorpusItem",
    "load_corpus",
    "strip_nuqta",
    "normalize_urdu",
    "to_ascii_digits",
    "render",
]

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORPUS_PATH = _FIXTURES / "urdu_corpus.json"


# ── Arm I vs arm J: the nuqta fold ───────────────────────────────────────────
#
# Devanagari nuqta (़) forms carry the Perso-Arabic phonemes Hindi orthography
# otherwise lacks: फ़/f/, ज़/z/, क़/q/, ग़/ɣ/, ख़/x/, ड़/ɽ/, ढ़/ɽʱ/. Dropping them
# is exactly what a naive "Urdu is just Hindi in another script" transliteration
# does, and it is the single controlled difference between arm I and arm J:
#
#   arm J (`devanagari`)              दफ़्तर  क़ीमत  ज़रूरी   — Urdu phonemes kept
#   arm I (`strip_nuqta(devanagari)`) दफ्तर   कीमत   जरूरी    — folded to Hindi
#
# If arm J beats arm I audibly, phoneme preservation matters and any future
# converter must produce nuqtas. If they are indistinguishable, the model is
# normalizing them away internally and a simpler converter suffices.
#
# Written as explicit \u escapes, not literal glyphs. Typing "क़" into a source
# file gives you whichever of the two encodings your editor happened to emit,
# and the decomposed one is TWO codepoints — which silently breaks
# `str.maketrans` (it requires length-1 keys) and would otherwise make this
# table's behaviour depend on how the file was saved.
_PRECOMPOSED_NUQTA: dict[int, str] = {
    0x0958: "क",  # क़ -> क
    0x0959: "ख",  # ख़ -> ख
    0x095A: "ग",  # ग़ -> ग
    0x095B: "ज",  # ज़ -> ज
    0x095C: "ड",  # ड़ -> ड
    0x095D: "ढ",  # ढ़ -> ढ
    0x095E: "फ",  # फ़ -> फ
    0x095F: "य",  # य़ -> य
    0x0931: "र",  # ऱ -> र
}

#: Combining nuqta U+093C — the decomposed spelling of the same graphemes.
_COMBINING_NUQTA = "़"


def strip_nuqta(text: str) -> str:
    """
    Fold Devanagari nuqta forms to their bare consonants (arm J -> arm I).

    Handles both spellings of the same grapheme: the precomposed codepoints
    (क़ U+0958 …) and the far more common decomposed form (क + ़ U+093C), which
    is what most editors and input methods actually produce.

    NFC normalization does NOT merge the two — Unicode marks U+0958..U+095F as
    composition exclusions specifically so round-tripping is stable — so both
    paths are handled explicitly here rather than delegated to `unicodedata`.
    """
    return text.translate(_PRECOMPOSED_NUQTA).replace(_COMBINING_NUQTA, "")


# ── Rung B of the ladder: Urdu Unicode normalization ─────────────────────────
#
# Character maps vendored from urduhack's `normalization.character` (MIT). The
# library itself is NOT a dependency: it targets Python 3.6-3.7, pulls
# TensorFlow, and is effectively unmaintained. The maps are small translation
# tables, so vendoring them is both cheaper and safer than adding it.
#
# The problem being fixed: Urdu text in the wild mixes true Urdu codepoints with
# Arabic ones that LOOK identical in most fonts. A tokenizer sees two different
# characters where a reader sees one.
_ARABIC_TO_URDU = str.maketrans({
    # YEH: Arabic yeh -> Farsi yeh (the Urdu form)
    "ي": "ی",  # ي -> ی
    "ى": "ی",  # ى -> ی
    # KAF: Arabic kaf -> keheh (the Urdu form)
    "ك": "ک",  # ك -> ک
    # HEH: Arabic heh -> heh goal (the Urdu form)
    "ه": "ہ",  # ه -> ہ
    # Presentation-form alef variants -> alef with madda
    "ﺁ": "آ",  # ﺁ -> آ
    "ﺂ": "آ",  # ﺂ -> آ
    # Arabic comma/semicolon/question already match Urdu usage; left alone.
})

#: Eastern Arabic-Indic digits ۰-۹ (U+06F0..U+06F9) -> ASCII.
_EASTERN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

#: Urdu diacritics (aerab): zabar, zer, pesh, shadda, sukun, tanween.
#: NOT stripped by `normalize_urdu` — see its docstring.
_DIACRITICS = "".join(chr(c) for c in range(0x064B, 0x0653)) + "ٰٕٔ"


def normalize_urdu(text: str, *, strip_diacritics: bool = False) -> str:
    """
    Rung B: fold Arabic character variants onto their Urdu equivalents.

    This is the one normalization with a clear mechanical justification — it
    merges codepoints that are the SAME letter to a reader but distinct to a
    tokenizer, so it can only reduce spurious vocabulary splits.

    `strip_diacritics` defaults to **False** on purpose. Zabar/zer/pesh encode
    the short vowels Perso-Arabic otherwise omits; removing them throws away
    pronunciation information, which is the opposite of what this bake-off
    wants. It is exposed only so the ladder can test the claim rather than
    assume it — the plan's rule is that any normalization which changes
    pronunciation gets rejected.

    Latin characters, digits and whitespace are untouched.
    """
    out = unicodedata.normalize("NFC", text).translate(_ARABIC_TO_URDU)
    if strip_diacritics:
        out = out.translate({ord(c): None for c in _DIACRITICS})
    return out


def to_ascii_digits(text: str) -> str:
    """
    Map Eastern Arabic-Indic digits ۰-۹ to ASCII 0-9.

    Held apart from `normalize_urdu` and OFF by default because it is the
    normalization most likely to hurt: it is a real possibility that a model
    trained on Urdu reads ۳ correctly and stumbles on 3, or vice versa. The
    corpus carries `num_ascii` / `num_eastern` as a controlled pair precisely
    so this can be measured instead of guessed.
    """
    return text.translate(_EASTERN_DIGITS)


# ── Corpus ───────────────────────────────────────────────────────────────────


class Representation:
    """The input forms an arm can feed a model. Values are corpus field names."""

    PERSO_ARABIC = "perso_arabic"
    ROMAN = "roman"
    #: Nuqta-preserving hand-authored gold (arm J).
    DEVANAGARI = "devanagari"
    #: Derived from DEVANAGARI by `strip_nuqta` (arm I).
    DEVANAGARI_PLAIN = "devanagari_plain"


@dataclass(frozen=True, slots=True)
class CorpusItem:
    id: str
    category: str
    stresses: tuple[str, ...]
    perso_arabic: str
    roman: str
    devanagari: str
    note: str = ""

    @property
    def devanagari_plain(self) -> str:
        return strip_nuqta(self.devanagari)

    @property
    def cer_reference(self) -> str:
        """
        Ground truth for CER, for EVERY arm regardless of input representation.

        The audio is Urdu speech in all arms, so the canonical Urdu string is
        the only defensible reference. Scoring an arm against whatever script it
        happened to be fed would make arms incomparable to each other.
        """
        return self.perso_arabic

    def text_for(
        self,
        representation: str,
        *,
        normalize: bool = False,
        ascii_digits: bool = False,
    ) -> str:
        """Render this item in `representation`, applying the ladder toggles."""
        if representation == Representation.DEVANAGARI_PLAIN:
            text = self.devanagari_plain
        else:
            text = getattr(self, representation)

        # Normalization is Perso-Arabic-specific; applying the Arabic->Urdu
        # character fold to Devanagari or Latin would be a no-op at best, so it
        # is gated rather than silently run over every script.
        if normalize and representation == Representation.PERSO_ARABIC:
            text = normalize_urdu(text)
        if ascii_digits:
            text = to_ascii_digits(text)
        return text


def load_corpus(path: Path | None = None) -> tuple[CorpusItem, ...]:
    """Load and validate the shared corpus. Raises on a malformed fixture."""
    raw = json.loads((path or CORPUS_PATH).read_text(encoding="utf-8"))
    items = tuple(
        CorpusItem(
            id=i["id"],
            category=i["category"],
            stresses=tuple(i["stresses"]),
            perso_arabic=i["perso_arabic"],
            roman=i["roman"],
            devanagari=i["devanagari"],
            note=i.get("note", ""),
        )
        for i in raw["items"]
    )
    declared = raw["_meta"]["item_count"]
    if len(items) != declared:
        raise ValueError(f"corpus declares {declared} items but contains {len(items)}")
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate corpus item ids")
    return items


def render(
    items: tuple[CorpusItem, ...],
    representation: str,
    *,
    normalize: bool = False,
    ascii_digits: bool = False,
) -> list[tuple[str, str]]:
    """`[(item_id, text), ...]` for one arm. The bake-off driver's entry point."""
    return [
        (i.id, i.text_for(representation, normalize=normalize, ascii_digits=ascii_digits))
        for i in items
    ]


if __name__ == "__main__":  # quick eyeball: python eval/urdu_represent.py
    import sys

    # Windows consoles default to cp1252, which cannot encode Urdu, Devanagari,
    # or box-drawing characters — printing any of this corpus raises
    # UnicodeEncodeError. The local dev machine is Windows, so this is the
    # normal case here, not an edge case.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    corpus = load_corpus()
    print(f"{len(corpus)} items\n")
    for item in corpus:
        print(f"-- {item.id}  [{item.category}]")
        print(f"   perso-arabic : {item.perso_arabic}")
        print(f"   roman        : {item.roman}")
        print(f"   devanagari(J): {item.devanagari}")
        print(f"   plain     (I): {item.devanagari_plain}")
        norm = item.text_for(Representation.PERSO_ARABIC, normalize=True)
        print(f"   normalized   : {'(unchanged)' if norm == item.perso_arabic else norm}")
        print()
