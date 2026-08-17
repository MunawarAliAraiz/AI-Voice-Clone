"""
AI Voice Clone Studio — the 'transliterate' job handler.

Roman Urdu (and, once gated, Devanagari) → Perso-Arabic script conversion.

WHY THIS IS A JOB AND NOT A SYNCHRONOUS ENDPOINT
--------------------------------------------------
Every other text operation here is synchronous precisely because it does not
touch the GPU — `POST /api/text/title` says so in its own docstring. This one
is the opposite: `TransliteratorScheduler.convert()` takes
`InferenceScheduler.exclusive_gpu()`, which empties the card and holds the GPU
slot for the whole of a ~78 s model load plus generation. Nothing that long,
and nothing that blocks every other generation on the box, belongs in a
request handler.

WHAT THIS HANDLER OWNS, AND WHAT IT REFUSES TO OWN
----------------------------------------------------
It owns the ORDER: convert, then validate. It does not own either step.
Conversion is the scheduler's (torch, a subprocess, a GPU); validation is
`domain/transliterate.py`'s (pure, no model needed to test it). Keeping them
apart is what makes "is this a transliteration or is it an answer" provable
without a 19 GB download.

A REJECTION IS A FAILED JOB, NOT A QUIET PASS
-----------------------------------------------
The instruction sent to the model is user-editable, so "the model answered the
text instead of converting it" is a reachable outcome, not a hypothetical. If
the validator refuses the output, this raises and the job lands FAILED with
the validator's own `reason` and `detail`. Returning the text anyway with a
warning attached would be exactly the silent-substitution failure golden rule
5 exists to prevent, one layer up from audio.

NO `route`, NO `generation_history` ROW
-----------------------------------------
Same as `analyze_llm.py`. This never calls `resolve()`, and the transliterator
is not in the audio catalog. Its output is EDITABLE TEXT a human reads and
corrects before generating — never a routing transform applied behind their
back, which is why `TransformKind` is untouched by this feature.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ...domain.transliterate import (
    MAX_INPUT_CHARS,
    TransliterationRejected,
    validate_transliteration,
)
from ...exceptions import TransliterationRejectedError, TransliteratorUnavailableError
from ..types import JobContext, JobOutcome, JobRecord

__all__ = ["TransliterateParams", "run_transliterate"]


class TransliterateParams(BaseModel):
    """
    Captured at enqueue by `POST /api/text/transliterate`. Re-validated here
    rather than trusted, the same discipline `SynthesizeParams` and
    `AnalyzeLlmParams` follow: a row written by an older build must fail
    loudly instead of being coerced into something plausible.
    """

    text: str = Field(..., max_length=MAX_INPUT_CHARS)
    #: The user's own addition to the system prompt. Editable on purpose — a
    #: user who knows their dialect can improve it — which is exactly why the
    #: validator below is NOT optional.
    instruction: str = Field("", max_length=2000)


async def run_transliterate(ctx: JobContext, job: JobRecord) -> JobOutcome:
    params = TransliterateParams.model_validate(job.params)

    if ctx.transliterator is None:
        # Infrastructure, not output quality. Names the setting, because the
        # fix is a deployment change and a generic "unavailable" would send
        # someone looking at the model instead.
        raise TransliteratorUnavailableError(
            "No transliterator is configured on this server "
            "(set VCS_GEMMA_TRANSLITERATOR_PYTHON and provision .venv-gemma)."
        )

    result = await ctx.transliterator.convert(
        text=params.text, instruction=params.instruction
    )

    try:
        check = validate_transliteration(params.text, result.text)
    except TransliterationRejected as exc:
        # FAILED, carrying the reason code. The client can then tell an echo
        # from an answer from a summary without parsing prose — and the user
        # never receives text that is not a conversion of what they wrote.
        raise TransliterationRejectedError(exc.detail, reason=exc.reason) from exc

    return JobOutcome(
        result={
            "text": result.text.strip(),
            "source_text": params.text,
            "gen_time_sec": result.gen_time_sec,
            "load_time_sec": result.load_time_sec,
            # The validator's measurements ride along rather than being
            # recomputed client-side: they are how a reviewer can see WHY
            # something passed, and a second implementation would drift.
            "arabic_share": check.arabic_share,
            "length_ratio": check.length_ratio,
        }
    )
