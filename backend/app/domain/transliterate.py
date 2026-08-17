"""
Script conversion: the contract, and the validator that enforces it.

Four conversions. Exactly one of them has passed a listening gate:

    latin      → perso_arabic   the SPEECH hop. GATED 2026-08-16.
    devanagari → roman          a Hindi-script transcript, made editable
    devanagari → perso_arabic   the same transcript, straight to speakable
    arabic     → roman          Urdu script, made editable

The last three are ungated. `latin → roman` is absent because it is a no-op,
and nothing converts INTO Devanagari — it is a source format only.

WHO PICKS THE TARGET
---------------------
The caller does, for a Devanagari source: the owner may want the transcript
readable (roman) or immediately speakable (perso_arabic), and that is a
preference about what they are about to do, not a fact about the text. What is
NOT the caller's is the SOURCE — that is detected here, because the user
declares the language and the code detects the script.

ENGLISH IS THE CASE THIS MODULE CANNOT SEE
--------------------------------------------
An English transcript needs no conversion at all, and nothing here can tell it
from Roman Urdu — both are Latin, this project's oldest documented trap. So
"don't offer conversion for English" is necessarily a decision made where the
language is *known* (the Composer's language field, the transcript panel's
detected script), never here. A caller that asks anyway gets a real conversion
of English into Urdu script, and that is a caller bug this cannot catch.

Pure. No I/O, no torch, no model — this module decides what a *valid*
conversion looks like, and `inference/transliterator_scheduler.py` decides how
to obtain one. Keeping the two apart is what makes the rule below testable
without a 19 GB model.

WHY A SERVER-SIDE VALIDATOR THE USER CANNOT SWITCH OFF
------------------------------------------------------
The instruction sent to the model is editable, because a user who knows their
own dialect can improve it. What is *not* editable is this: the output is only
accepted if it is a plausible transliteration of the input rather than an
answer to it.

That distinction is the whole risk of the feature. An instruction-following
model handed "Kal office mein meeting hai" can transliterate it, or it can
translate it, summarize it, answer it, refuse it, or continue the
conversation — and every one of those returns fluent, confident Urdu that
looks right in a text box. The A3 runs are the evidence this is not
hypothetical: run 2's defects were all VALID URDU WORDS meaning something
else (کال *call* for کل *tomorrow*, طباعت *printing* for طبیعت *health*).
A validator cannot catch a wrong word. It can catch the output not being a
transliteration at all, which is the failure mode an editable instruction
introduces and the one the user cannot be expected to police.

AND IT CATCHES LESS ON THE ROMAN SIDE
--------------------------------------
Everything above assumes the target script is recognisable. For the transcript
hop it is not — Roman Urdu and English are both Latin. See
`MAX_RESIDUAL_SOURCE_SHARE` for what that costs and why the answer is a
product decision (a human reads the Roman draft) rather than a threshold.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
It does not score quality, and it must never be mistaken for the gate.
Phase A settled that four times over: numeric screens can only fail a
candidate, never approve one. Passing here means "this is a transliteration",
not "this is a good one" — the output goes into an editable field precisely
because the human is the judge.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TransliterationRejected",
    "MAX_INPUT_CHARS",
    "MIN_DEVANAGARI_SHARE",
    "MAX_RESIDUAL_SOURCE_SHARE",
    "TARGET_PERSO_ARABIC",
    "TARGET_ROMAN",
    "SUPPORTED_PAIRS",
    "DEFAULT_TARGETS",
    "source_script_of",
    "target_script_for",
    "validate_transliteration",
]

#: The targets, matching `runtimes/gemma_transliterator.py`'s constants.
TARGET_PERSO_ARABIC = "perso_arabic"
TARGET_ROMAN = "roman"

#: Ceiling on what may be sent PER CHUNK. Not a safety rule — a latency one.
#: Generation measured 2.7-5.1 s for a sentence and scales with length, and a
#: batch runs its chunks in sequence against one residency, so an oversized
#: single chunk is the thing that makes a batch unpredictable.
MAX_INPUT_CHARS = 2000

#: A transliteration preserves roughly one sound per sound, so its length
#: tracks the input's within a wide band. Perso-Arabic drops short vowels and
#: so runs SHORTER than Roman Urdu — "aap kaise hain" (14) vs "آپ کیسے ہیں"
#: (11) — while diacritics and spelled-out loanwords push the other way.
#:
#: The bounds are deliberately loose. They exist to catch a model that
#: answered the question, translated the text, or emitted a paragraph of
#: commentary — outputs that miss by multiples, not by percentages. Tightening
#: them to reject "slightly off" lengths would start rejecting correct
#: conversions, and a false rejection here costs a ~19 GB model load to retry.
MIN_LENGTH_RATIO = 0.4
MAX_LENGTH_RATIO = 2.5

#: This detects "NO conversion happened", not "the output is mostly Urdu" —
#: and the difference is the whole design of this check.
#:
#: Measured on realistic strings rather than chosen by intuition, which was
#: wrong: a 0.5 floor rejected a correct code-switched conversion.
#:
#:     1.000  plain conversion            السلام علیکم، کیا حال ہے آپ کا؟
#:     0.467  ordinary code-switch        کل office میں meeting ہے …
#:     0.115  heavy code-switch           Meeting کے baad pull request …
#:     0.000  English answer / refusal / echoed input
#:
#: Legitimate output spans 0.115–1.000 because the A3 contract explicitly
#: says to KEEP English words in Latin and this corpus is 57% code-switched
#: — so a correct conversion can be mostly Latin characters. Every failure
#: mode, by contrast, contains no Perso-Arabic at all. The gap is between
#: "some" and "none", so that is where the threshold goes; anything higher
#: rejects the very code-switching the prompt asks for.
MIN_ARABIC_SHARE = 0.05

#: The Roman target's check, and it is the MIRROR of MIN_ARABIC_SHARE rather
#: than an analogue of it — because it cannot be an analogue.
#:
#: For a Perso-Arabic target, "did a conversion happen" is answerable by looking
#: for the target script, because the target script is unmistakable. For a Roman
#: target it is not: **Roman Urdu and English are both Latin**, which is this
#: project's oldest documented trap. "Aap kaise hain" and "How are you" are
#: indistinguishable to any script test, so no threshold on Latin can separate a
#: correct conversion from an English translation.
#:
#: So this asks the only question that IS answerable: is the SOURCE script gone?
#: A Devanagari echo has ~1.0 Devanagari; a real conversion has ~0.0. Same
#: "some versus none" gap, measured from the other end.
#:
#: WHAT THIS CANNOT CATCH, STATED PLAINLY: a translation into English. It passes
#: every check here — right script, right length, source script absent. The
#: validator is structurally weaker for this direction and no threshold fixes
#: it. What covers it instead is the product: the Roman output exists precisely
#: so a person reads and edits it before anything is generated from it. That is
#: an argument about where the human sits in the loop, not a claim that the
#: check is as strong as the other one.
MAX_RESIDUAL_SOURCE_SHARE = 0.05

#: Any real Devanagari at all means the Devanagari exemplars are the right ones.
#:
#: NOT a dominance test, and that is the point. `detect_script` returns MIXED
#: when no single script clears its threshold, which is the ordinary shape of a
#: Hindi caption carrying English words — and MIXED would fall back to Latin,
#: showing the model six `Roman:` examples for text it cannot read as Roman.
#: The question here is not "which script wins", it is "which exemplar set has
#: anything to say about this input", and the Latin set has nothing to say
#: about Devanagari. So presence decides, not dominance.
#:
#: The floor exists only to ignore a stray character — one Devanagari glyph
#: quoted inside Roman Urdu should not swing the whole prompt. Same 5% and same
#: reasoning as MIN_ARABIC_SHARE above: the gap being measured is between
#: "some" and "none".
MIN_DEVANAGARI_SHARE = 0.05


def _share(text: str, ranges: tuple[tuple[str, str], ...]) -> float:
    """
    Share of *letters* falling in any of `ranges`.

    Letters only: counting digits, spaces and punctuation would let a mostly
    numeric line pass or fail on its formatting rather than its script.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    hits = sum(1 for c in letters if any(lo <= c <= hi for lo, hi in ranges))
    return hits / len(letters)


_ARABIC_RANGES = (("؀", "ۿ"), ("ݐ", "ݿ"))
_DEVANAGARI_RANGES = (("ऀ", "ॿ"),)

#: Source script → the ranges that mean "this was NOT converted", for a target
#: whose own script cannot be recognised. Latin is absent because no conversion
#: leaves text in Latin as a failure: `(latin, perso_arabic)` is judged by
#: `MIN_ARABIC_SHARE` instead, and there is no `(latin, roman)`.
_RESIDUAL_RANGES = {
    "devanagari": _DEVANAGARI_RANGES,
    "arabic": _ARABIC_RANGES,
}


def _arabic_share(text: str) -> float:
    return _share(text, _ARABIC_RANGES)


def source_script_of(text: str) -> str:
    """
    Which script `text` is written in, for the purpose of converting it.

    Returns `devanagari`, `arabic` or `latin` — the plain strings the wire
    protocol and `runtimes/gemma_transliterator.py` use. Pure, so the decision
    is testable without a GPU; server-side, so a client never has to encode it.

    Presence, not dominance, and it is checked in that order: Devanagari first,
    then Perso-Arabic, then Latin as the remainder. `detect_script` would return
    MIXED for the ordinary shape of either a Hindi or an Urdu caption carrying
    English words, and MIXED answers a different question than this one. What is
    being asked here is "which exemplar set has anything to SAY about this
    input", and a set has nothing to say about a script it never demonstrates.
    """
    if _share(text, _DEVANAGARI_RANGES) >= MIN_DEVANAGARI_SHARE:
        return "devanagari"
    if _share(text, _ARABIC_RANGES) >= MIN_ARABIC_SHARE:
        return "arabic"
    return "latin"


#: Every conversion the transliterator knows, as `(source, target)`.
#:
#: Absent on purpose:
#:   `(latin, roman)`        a no-op.
#:   `(arabic, perso_arabic)` a no-op.
#:   anything `→ devanagari`  Devanagari is a SOURCE FORMAT here, never a
#:                            target — the whole reason `hi` is not a
#:                            `LanguageCode` and `routing.py` refuses to render
#:                            it. Adding it would reopen that by the back door.
SUPPORTED_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("latin", TARGET_PERSO_ARABIC),
    ("devanagari", TARGET_ROMAN),
    ("devanagari", TARGET_PERSO_ARABIC),
    ("arabic", TARGET_ROMAN),
})

#: Where each source goes when the caller expresses no preference.
#:
#: A DEFAULT, not a rule — for a Devanagari transcript the owner genuinely
#: chooses, because "readable" and "speakable" are different things to want and
#: only they know which they are about to do. Roman is the default there
#: because the transcript path exists to be EDITED; a caption YouTube's ASR
#: guessed at is a draft, and converting a draft straight to speech skips the
#: step the feature was asked for.
DEFAULT_TARGETS: dict[str, str] = {
    "latin": TARGET_PERSO_ARABIC,
    "devanagari": TARGET_ROMAN,
    "arabic": TARGET_ROMAN,
}


def target_script_for(source_script: str) -> str:
    """The default target for `source_script`. See `DEFAULT_TARGETS`."""
    return DEFAULT_TARGETS.get(source_script, TARGET_PERSO_ARABIC)


class TransliterationRejected(Exception):
    """
    The model's output is not a transliteration of the input.

    Carries `reason` for the log and `detail` for the user, because the two
    want different things: the log wants the measurement, the user wants to
    know whether to retry or to edit their instruction.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TransliterationCheck:
    """
    What the validator measured. Returned so the API can surface it.

    `arabic_share` is measured for BOTH targets even though only one is judged
    on it — for a Roman target it is diagnostic rather than a gate, and a
    reviewer looking at a bad conversion wants the number either way.
    """

    arabic_share: float
    length_ratio: float
    #: Share of the output still in the SOURCE script. The Roman target's gate;
    #: recorded for the Perso-Arabic target too, where it is diagnostic.
    residual_source_share: float = 0.0


def _reject_script(text: str, source: str, reason: str, wrong_script_detail: str) -> None:
    """
    Raise the script failure, naming the likeliest cause.

    An echo and prose want different messages because the user's next move
    differs: an echo means the instruction was ignored outright, prose means it
    was obeyed as the wrong instruction. Equality alone IS the echo — no script
    test. Requiring `Script.LATIN` (as this once did) made a Devanagari echo
    report as "replied in the wrong script": true, and it sends the user to fix
    the wrong thing.
    """
    raise TransliterationRejected(
        reason,
        "The model echoed your text back instead of converting it."
        if text == source.strip()
        else wrong_script_detail,
    )


def validate_transliteration(
    source: str, output: str, target: str = TARGET_PERSO_ARABIC
) -> TransliterationCheck:
    """
    Accept `output` as a transliteration of `source`, or raise.

    Checks, in the order a failure is most likely:

    1. Non-empty. A model that returned nothing is a failure, not an empty
       transliteration.
    2. The script check — **and it is a different question per target.**
       Perso-Arabic: is the target script PRESENT? Roman: is the source script
       GONE? See `MAX_RESIDUAL_SOURCE_SHARE` for why the Roman one cannot be
       phrased the first way, and for exactly what it therefore cannot catch.
    3. Length in band. Catches an answer, a summary, or a commentary — all of
       which miss by multiples.

    The source is only ever measured, never trusted as correct: this says
    nothing about whether the conversion is *right*, which is what the editable
    output field and the human reading it are for. That is doubly true for the
    Roman target, where check 2 is genuinely weaker.
    """
    text = output.strip()
    if not text:
        raise TransliterationRejected(
            "empty", "The model returned nothing. Try again, or simplify the text."
        )

    arabic = _arabic_share(text)
    residual = _share(text, _RESIDUAL_RANGES.get(source_script_of(source), ()))

    if target == TARGET_ROMAN:
        if residual > MAX_RESIDUAL_SOURCE_SHARE:
            _reject_script(
                text, source, "not_converted",
                "The model left your text in its original script instead of "
                "writing it in Latin letters.",
            )
    elif arabic < MIN_ARABIC_SHARE:
        _reject_script(
            text, source, "not_urdu_script",
            "The model replied in the wrong script instead of converting your text.",
        )

    src = source.strip()
    ratio = len(text) / len(src) if src else 0.0
    if ratio < MIN_LENGTH_RATIO:
        raise TransliterationRejected(
            "too_short",
            "The model shortened your text instead of converting it — "
            "it may have summarized rather than transliterated.",
        )
    if ratio > MAX_LENGTH_RATIO:
        raise TransliterationRejected(
            "too_long",
            "The model wrote more than your text — "
            "it may have answered it rather than converting it.",
        )

    return TransliterationCheck(
        arabic_share=arabic, length_ratio=ratio, residual_source_share=residual
    )
