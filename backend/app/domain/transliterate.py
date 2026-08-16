"""
Roman Urdu → Perso-Arabic conversion: the contract, and the validator that
enforces it.

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

from .language import Script, detect_script

__all__ = [
    "TransliterationRejected",
    "MAX_INPUT_CHARS",
    "validate_transliteration",
]

#: Ceiling on what may be sent for conversion. Not a safety rule — a latency
#: one. Gemma-4-31B holds the GPU exclusively for the whole conversion (see
#: `InferenceScheduler.exclusive_gpu`), so a very long input blocks every
#: generation on the box, not just this request.
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
    """What the validator measured. Returned so the API can surface it."""

    arabic_share: float
    length_ratio: float


def _arabic_share(text: str) -> float:
    """
    Share of *letters* that are Perso-Arabic.

    Letters only: counting digits, spaces and punctuation would let a mostly
    numeric line pass or fail on its formatting rather than its script.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic = sum(1 for c in letters if "؀" <= c <= "ۿ" or "ݐ" <= c <= "ݿ")
    return arabic / len(letters)


def validate_transliteration(source: str, output: str) -> TransliterationCheck:
    """
    Accept `output` as a transliteration of `source`, or raise.

    Checks, in the order a failure is most likely:

    1. Non-empty. A model that returned nothing is a failure, not an empty
       transliteration.
    2. Predominantly Perso-Arabic. Catches "I cannot help with that", a model
       that echoed the Roman input back unchanged, and an English translation.
    3. Length in band. Catches an answer, a summary, or a commentary — all of
       which miss by multiples.

    The source is only ever measured, never trusted as correct: this says
    nothing about whether the conversion is *right*, which is what the
    editable output field and the human reading it are for.
    """
    text = output.strip()
    if not text:
        raise TransliterationRejected(
            "empty", "The model returned nothing. Try again, or simplify the text."
        )

    share = _arabic_share(text)
    if share < MIN_ARABIC_SHARE:
        # Distinguish the single most likely cause, because the fix differs:
        # an echo means the instruction was ignored, prose means it was
        # answered.
        echoed = detect_script(text)[0] is Script.LATIN and text == source.strip()
        raise TransliterationRejected(
            "not_urdu_script",
            "The model echoed your text back instead of converting it."
            if echoed
            else "The model replied in the wrong script instead of converting your text.",
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

    return TransliterationCheck(arabic_share=share, length_ratio=ratio)
