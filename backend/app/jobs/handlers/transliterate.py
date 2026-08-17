"""
AI Voice Clone Studio — the 'transliterate' job handler.

Script conversion, in whichever direction the text and the caller call for.

THE SOURCE IS DETECTED. THE TARGET IS CHOSEN.
-----------------------------------------------
    Roman Urdu   → Perso-Arabic   so OmniVoice can say it
    Devanagari   → Roman Urdu     so the OWNER can read and edit it
    Devanagari   → Perso-Arabic   straight to speakable, no edit step
    Perso-Arabic → Roman Urdu     an Urdu-script caption, made editable

The source comes from the text and is never taken from the request — the user
declares the language, the code detects the script, the same rule the
transcript endpoint follows. The target is the caller's, because "readable" and
"speakable" are different things to want and only they know which they are
about to do. It is resolved at ENQUEUE and read off the row here.

THE FOUR ARE NOT EQUALLY TRUSTED, AND THE CODE SAYS SO
--------------------------------------------------------
Roman → Perso-Arabic is what A3 run 3 passed by ear. **The other three have
never passed a listening gate**; R4b measured hop chains compounding errors,
and the edit-then-speak route now takes two hops rather than one.
`source_script` and `target_script` both ride out on the result for that
reason: a conversion from an ungated pair must be identifiable as one rather
than looking like any other successful job, and no UI may present it as working
until the owner has listened.

The validator is also weaker for a ROMAN target, structurally and unfixably —
Roman Urdu and English are both Latin, so no check can tell a conversion from a
translation. `domain/transliterate.MAX_RESIDUAL_SOURCE_SHARE` states exactly
what that costs. It is covered by a person reading the draft, which is the
whole reason a Roman target exists.

WHY THIS IS A JOB AND NOT A SYNCHRONOUS ENDPOINT
--------------------------------------------------
Every other text operation here is synchronous precisely because it does not
touch the GPU — `POST /api/text/title` says so in its own docstring. This one
does. Gemma is resident so the usual cost is ~5 s per chunk, but a COLD load is
150-330 s and holds the audio scheduler's GPU slot throughout. An operation
that is usually seconds and occasionally five minutes belongs in the queue, and
a transcript's worth of chunks belongs there without argument.

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

from typing import Literal

from pydantic import BaseModel, Field

from ...domain.transliterate import (
    MAX_BATCH_CHUNKS,
    TransliterationRejected,
    source_script_of,
    target_script_for,
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

    #: Always a LIST on the row, even for a single passage. The endpoint
    #: normalizes, so this layer never branches on which shape arrived.
    texts: list[str] = Field(..., max_length=MAX_BATCH_CHUNKS)
    #: The user's own addition to the system prompt. Editable on purpose — a
    #: user who knows their dialect can improve it — which is exactly why the
    #: validator below is NOT optional.
    instruction: str = Field("", max_length=2000)
    #: Resolved at ENQUEUE and stored on the row, never re-derived here.
    #:
    #: Exactly golden rule 8's argument for `route_json`: a handler that decides
    #: again at claim time can decide differently from what the client was told,
    #: and the client already has a 202 saying which conversion it asked for.
    #: Optional only so a row written before this field existed still runs.
    target: Literal["roman", "perso_arabic"] | None = None


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

    # The SOURCE is detected here and never taken from the request — the user
    # declares the language, the code detects the script, the same rule the
    # transcript endpoint's `script` field follows. Detected from the WHOLE
    # batch joined, not per chunk: a transcript is one document in one script,
    # and a short chunk that happens to be all English would otherwise pick a
    # different exemplar set than the chunk before it.
    source_script = source_script_of(" ".join(params.texts))
    target_script = params.target or target_script_for(source_script)

    results = await ctx.transliterator.convert_many(
        texts=params.texts,
        instruction=params.instruction,
        source_script=source_script,
        target_script=target_script,
    )

    items: list[dict] = []
    rejected = 0
    first_rejection: TransliterationRejected | None = None
    for index, (source_text, result) in enumerate(zip(params.texts, results, strict=True)):
        try:
            check = validate_transliteration(source_text, result.text, target_script)
        except TransliterationRejected as exc:
            # NO TEXT ON A REJECTED ITEM. That is golden rule 5 at the item
            # level: the user never receives a string that is not a conversion
            # of what they wrote, whether it arrived alone or as chunk 37 of a
            # transcript.
            rejected += 1
            first_rejection = first_rejection or exc
            items.append({
                "index": index,
                "status": "rejected",
                "source_text": source_text,
                "reason": exc.reason,
                "detail": exc.detail,
            })
            continue
        items.append({
            "index": index,
            "status": "ok",
            "text": result.text.strip(),
            "source_text": source_text,
            # The validator's measurements ride along rather than being
            # recomputed client-side: they are how a reviewer can see WHY
            # something passed, and a second implementation would drift.
            "arabic_share": check.arabic_share,
            "length_ratio": check.length_ratio,
            "residual_source_share": check.residual_source_share,
        })

    # ALL REJECTED -> the job FAILS, carrying the first reason code.
    #
    # This is what preserves the single-item behaviour exactly: one chunk
    # rejected IS all chunks rejected, so a lone bad conversion still fails
    # loudly rather than succeeding with an empty-looking result. What it adds
    # is that chunk 37 of a transcript does not throw away the 44 that
    # converted — those are real work the user can use, and each bad one is
    # marked in place with its reason rather than silently dropped.
    if rejected == len(items) and first_rejection is not None:
        raise TransliterationRejectedError(
            first_rejection.detail, reason=first_rejection.reason
        )

    return JobOutcome(
        result={
            "items": items,
            "ok_count": len(items) - rejected,
            "rejected_count": rejected,
            #: Which conversion this was. The pair is the thing that was gated
            #: or not: only latin -> perso_arabic has passed a listening gate,
            #: and a result from another pair must be identifiable as such
            #: rather than looking like any other successful job.
            "source_script": source_script,
            "target_script": target_script,
            # Charged once, by `convert_many`, because the load happened once.
            "load_time_sec": sum(r.load_time_sec for r in results),
            "gen_time_sec": sum(r.gen_time_sec for r in results),
        }
    )
