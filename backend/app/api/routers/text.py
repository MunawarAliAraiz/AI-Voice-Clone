"""
Text assistance: short titles, and Roman -> Perso-Arabic transliteration.

TWO ENDPOINTS, TWO SHAPES, AND THE LINE BETWEEN THEM IS THE GPU
----------------------------------------------------------------
`/title` is SYNCHRONOUS. `/transliterate` is a JOB (202 + poll). The
difference is not text length or latency taste — it is that one of them takes
the whole GPU and the other never touches it. Read both arguments before
adding a third endpoint here.

WHY `/title` IS SYNCHRONOUS
---------------------------
Every other model-backed operation here is a job (202 + poll) because it takes
tens of seconds and produces audio. This produces two words on an
ALREADY-RESIDENT worker. Putting it behind the job queue would mean a second
polling flow, a second `JobKind`, and a `jobs` row per title — all to deliver a
string the client needs before it can enqueue the thing it actually came for.

WHY `/transliterate` IS NOT
---------------------------
Because of the COLD case, which is the one that decides. Resident, a conversion
is ~5 s; from cold it is 150-330 s while ~19 GB of weights load, and the load
holds the audio scheduler's GPU slot so nothing else generates meanwhile. A
request that is usually fast and occasionally five minutes is a job, not an
endpoint — and a batch of transcript chunks is unambiguously one.

WHY IT CALLS `classify()` RATHER THAN A TITLE-ONLY OP
-----------------------------------------------------
The analyzer returns the title in the SAME response as its prosody rows
(`runtimes/qwen_analyzer.py`), so this is one generation on an
already-resident worker. The alternative — a dedicated title prompt — would
have meant two prompts to keep in step for no saving, since the ~6 GB load
dominates either path and the worker is shared regardless.

A side effect worth knowing: the rows are computed and discarded here. That is
deliberate. Splitting them out would double the prompts to maintain, and the
Composer's own AI-suggest press already keeps its rows.

THE FALLBACK IS COSMETIC, AND THAT IS WHY IT IS ALLOWED
--------------------------------------------------------
If the analyzer is unavailable or its output fails validation, this returns the
first few words of the text with `source="text"`. Golden rules 1 and 5 forbid
silently substituting AUDIO a model did not produce, and silently ROUTING
somewhere the caller did not ask for. A label on a list row is neither, and the
response says which path produced it rather than hiding the difference.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from ...config import Settings
from ...db import Database
from ...domain.direction_analyze import analyze
from ...domain.transliterate import (
    SUPPORTED_PAIRS,
    source_script_of,
    target_script_for,
)
from ...exceptions import TransliteratorUnavailableError, UnsupportedConversionError
from ...inference.protocol import SchedulerProtocol
from ...jobs import JobKind, JobRunner
from ..deps import get_db, get_job_runner, get_scheduler, get_settings
from ..schemas.jobs import JobStatusResponse
from ..schemas.text import TitleRequest, TitleResponse, TransliterateRequest
from .jobs import build_job_status_response

router = APIRouter(prefix="/text", tags=["text"])
logger = logging.getLogger(__name__)

#: Words taken from the text when the analyzer cannot supply a title. Four is
#: enough to tell two generations apart in a list and short enough to stay a
#: label rather than becoming the text again.
_FALLBACK_WORDS = 4


def fallback_title(text: str) -> str:
    """First few words, whitespace-collapsed. Never empty for non-empty text."""
    words = text.split()
    return " ".join(words[:_FALLBACK_WORDS])


@router.post("/transliterate", response_model=JobStatusResponse, status_code=202)
async def transliterate(
    body: TransliterateRequest,
    request: Request,
    response: Response,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> JobStatusResponse:
    """
    Convert text between scripts. **202 + poll**, not synchronous.

    The source is detected from the text; the target is `body.target`, or this
    source's usual destination when that is omitted. An unsupported pair is
    refused HERE, at enqueue, rather than becoming a failed job forty seconds
    into a 19 GB model load — the request is wrong, not the conversion.

    THE ONE THING THAT MAKES THIS DIFFERENT FROM EVERY OTHER TEXT ENDPOINT:
    the module docstring above explains why a short text op should be
    synchronous, and every word of it holds for operations that never touch the
    GPU. This one does. Gemma is resident, so the usual cost is ~5 s — but a
    COLD load is 150-330 s during which it holds the audio scheduler's slot,
    and that is what the job queue exists for.

    `route=None`, like `ANALYZE_LLM`: this never calls `resolve()` and the
    transliterator is not in the audio catalog. Its output is editable text a
    human approves — `TransformKind` and `domain/routing.py` are untouched by
    this feature, which is what keeps golden rules 4 and 5 intact while still
    letting Roman Urdu reach OmniVoice.

    The 202 body is the same shape as `POST /api/generate`'s, built by the same
    `build_job_status_response`, so the client polls the existing generic
    `GET /api/jobs/{id}` with no new polling flow.
    """
    # Refused HERE when the server has no transliterator, rather than enqueued
    # and failed later. The reason is a deployment fact known at startup, so
    # making the user wait in a queue to be told the feature does not exist
    # here would be a worse version of the same answer.
    if getattr(request.app.state, "transliterator", None) is None:
        raise TransliteratorUnavailableError(
            getattr(request.app.state, "transliterator_reason", None)
            or "Script conversion is not available on this server."
        )

    texts = body.as_list()
    source = source_script_of(" ".join(texts))
    target = body.target or target_script_for(source)
    if (source, target) not in SUPPORTED_PAIRS:
        raise UnsupportedConversionError(source, target)

    job = await runner.enqueue(
        JobKind.TRANSLITERATE,
        params={
            "texts": texts,
            "instruction": body.instruction,
            # Resolved once, HERE, and stored on the row. The handler must not
            # re-derive it: same argument as golden rule 8's "route is resolved
            # at enqueue" -- a job that decides what it is at claim time can
            # decide differently than what the client was told.
            "target": target,
        },
        route=None,
        profile_id=None,
    )
    return await build_job_status_response(job, db, settings, scheduler, response)


@router.post("/title", response_model=TitleResponse)
async def suggest_title(body: TitleRequest, request: Request) -> TitleResponse:
    analyzer = getattr(request.app.state, "analyzer", None)
    if analyzer is None:
        return TitleResponse(title=fallback_title(body.text), source="text")

    plan = analyze(body.text, body.language)
    try:
        result = await analyzer.classify(
            language=body.language, sentences=tuple(seg.text for seg in plan.segments)
        )
    except Exception:
        # Includes AnalyzerUnavailableError (no venv configured) and a worker
        # that died. Logged at exception level because a persistently failing
        # analyzer is worth noticing even though it never blocks a generation.
        logger.exception("title suggestion via the analyzer failed; using the text")
        return TitleResponse(title=fallback_title(body.text), source="text")

    title = result.title.strip()
    if not title:
        return TitleResponse(title=fallback_title(body.text), source="text")
    return TitleResponse(title=title, source="analyzer")
