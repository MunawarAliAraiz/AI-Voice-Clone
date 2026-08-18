"""
Metrics for the Roman Urdu -> Perso-Arabic conversion probe.

WHY CER IS NOT ENOUGH
---------------------
The output contract is "change the script, not the words": Urdu content becomes
Perso-Arabic, intentional English code-switch tokens stay verbatim in Latin.
CER cannot express either half of that.

  - It buries a translation. `آفس` -> `کامگاہ` (office -> "workplace") is a
    handful of characters inside a long sentence, so CER barely moves while the
    single most important rule has been broken.
  - It cannot see a half-converted sentence. `میں office ja raha hoon` scores
    better than gibberish and is completely unusable.

So two metrics carry the contract and CER is demoted to a breakage screen.

  code-switch preservation  did the Latin the gold KEEPS survive verbatim?
  conversion completeness   did the Urdu content actually BECOME Perso-Arabic?

Conversion completeness is deliberately NOT "does some Arabic appear". That
test passes `میں office ja raha hoon`, which is wrong. Instead: take the output's
Latin tokens and subtract the gold's Latin tokens. **Whatever is left over is
Urdu content the model failed to convert.** The corpus makes this measurable
because its gold already encodes which tokens are legitimately Latin.

CASE IS PART OF THE CONTRACT, BUT ONLY FOR PRESERVATION
-------------------------------------------------------
The corpus deliberately contains `WhatsApp` (internal capital) and `git`
(lowercase command name), and its notes call lowercasing `git` a
preservation failure. So preservation is compared case-SENSITIVELY.

Residue is compared case-INSENSITIVELY on purpose. If the model emits `Git` for
gold's `git`, that is a casing error, not "Urdu content left in Latin" -- and
counting it as residue would corrupt the number that is supposed to mean
"unconverted Urdu". Case slips are reported separately instead of being folded
into either headline metric.

Self-check: `python eval/translit_metrics.py`
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

__all__ = ["ContractScore", "score_contract", "latin_tokens", "arabic_tokens"]

#: A "Latin token" is a maximal run of ASCII letters. Punctuation and digits are
#: excluded deliberately: digits are handled by the separate number
#: normalization layer, and attaching `.` or `,` to a token would make
#: `office,` and `office` different words.
_LATIN_RUN = re.compile(r"[A-Za-z]+")

#: Arabic-script runs, used only for the coarse "did any conversion happen"
#: floor and for reporting share-vs-gold. Perso-Arabic Urdu lives in the Arabic
#: block plus the Arabic Supplement/Extended-A ranges.
_ARABIC_RUN = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]+")


def latin_tokens(text: str) -> list[str]:
    return _LATIN_RUN.findall(unicodedata.normalize("NFC", text))


def arabic_tokens(text: str) -> list[str]:
    return _ARABIC_RUN.findall(unicodedata.normalize("NFC", text))


@dataclass
class ContractScore:
    """One (output, gold) pair scored against the output contract."""

    #: Fraction of the gold's Latin tokens present verbatim in the output.
    #: 1.0 when the gold has no Latin tokens (nothing to preserve, not a fail).
    code_switch_preservation: float
    #: Gold Latin tokens missing from the output entirely. These are the
    #: translated-away or dropped ones -- the `آفس` -> `کامگاہ` failure.
    lost_code_switch: list[str] = field(default_factory=list)
    #: Present but recased (`Git` for `git`). A real contract miss, kept apart
    #: so it cannot be confused with a translation or with unconverted Urdu.
    recased_code_switch: list[str] = field(default_factory=list)

    #: Output Latin tokens that the gold does NOT have in Latin. This is Urdu
    #: content the model left unconverted (or English it invented).
    unconverted_residue: list[str] = field(default_factory=list)
    #: 1.0 when there is no residue, falling towards 0 as more of the output's
    #: Latin is unaccounted for. The headline "did it actually convert" number.
    conversion_completeness: float = 1.0

    #: Share of tokens that are Arabic-script, output vs gold. Context for the
    #: two numbers above, not a gate -- a model can hit the right share while
    #: converting the wrong words.
    arabic_share_output: float = 0.0
    arabic_share_gold: float = 0.0

    @property
    def contract_ok(self) -> bool:
        """Both halves of the contract satisfied, ignoring spelling quality."""
        return (
            self.code_switch_preservation == 1.0
            and not self.unconverted_residue
            and not self.recased_code_switch
        )


def _share_arabic(text: str) -> float:
    arabic = sum(len(t) for t in arabic_tokens(text))
    latin = sum(len(t) for t in latin_tokens(text))
    total = arabic + latin
    return arabic / total if total else 0.0


def score_contract(output: str, gold: str) -> ContractScore:
    """
    Score one conversion against its gold.

    `gold` supplies the ground truth for *which* tokens are legitimately Latin.
    That is the whole reason this is measurable rather than a matter of taste --
    see the corpus's `latin_islands` note.
    """
    gold_latin = latin_tokens(gold)
    out_latin = latin_tokens(output)

    out_exact = set(out_latin)
    out_folded = {t.casefold() for t in out_latin}

    lost, recased, preserved = [], [], 0
    for token in gold_latin:
        if token in out_exact:
            preserved += 1
        elif token.casefold() in out_folded:
            # Survived as a word but not as written. Counts against the
            # contract, but it is a casing bug, not a translation.
            recased.append(token)
        else:
            lost.append(token)

    preservation = preserved / len(gold_latin) if gold_latin else 1.0

    # Residue: output Latin that the gold does not license. Case-insensitive,
    # so a recased token is attributed to `recased` above and never
    # double-counted here as unconverted Urdu.
    gold_folded = {t.casefold() for t in gold_latin}
    residue = [t for t in out_latin if t.casefold() not in gold_folded]

    # Denominator is the output's own Latin, so "left half the sentence in
    # Latin" reads as a low number regardless of how long the gold is.
    completeness = 1.0 - (len(residue) / len(out_latin)) if out_latin else 1.0

    return ContractScore(
        code_switch_preservation=round(preservation, 4),
        lost_code_switch=lost,
        recased_code_switch=recased,
        unconverted_residue=residue,
        conversion_completeness=round(completeness, 4),
        arabic_share_output=round(_share_arabic(output), 4),
        arabic_share_gold=round(_share_arabic(gold), 4),
    )


def _self_check() -> None:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    gold = "میں office جا رہا ہوں"
    cases = [
        # (label, output, expect_ok, note)
        ("perfect", "میں office جا رہا ہوں", True, "Urdu converted, English kept"),
        ("nothing converted", "main office ja raha hoon", False,
         "no Arabic at all -- must fail on residue, not just on script presence"),
        ("half converted", "میں office ja raha hoon", False,
         "THE case a bare Arabic-presence test wrongly passes"),
        ("translated the English", "میں دفتر جا رہا ہوں", False,
         "office lost -- the failure CER buries"),
        ("recased", "میں Office جا رہا ہوں", False, "survived, but not as written"),
    ]
    print(f"gold: {gold}\n")
    failed = 0
    for label, out, expect_ok, note in cases:
        s = score_contract(out, gold)
        ok = s.contract_ok
        mark = "OK " if ok == expect_ok else "!! "
        if ok != expect_ok:
            failed += 1
        print(f"{mark}{label:<22} contract_ok={ok!s:<5} "
              f"preserve={s.code_switch_preservation} complete={s.conversion_completeness}")
        print(f"    lost={s.lost_code_switch} recased={s.recased_code_switch} "
              f"residue={s.unconverted_residue}")
        print(f"    {note}\n")

    # The distinction the whole module exists for.
    half = score_contract("میں office ja raha hoon", gold)
    assert half.arabic_share_output > 0, "half-converted output DOES contain Arabic"
    assert half.unconverted_residue, "...and must still fail on residue"
    print("verified: a bare Arabic-presence test passes the half-converted case; "
          "residue catches it.")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    _self_check()
