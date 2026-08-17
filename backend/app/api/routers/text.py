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
It runs inside `InferenceScheduler.exclusive_gpu()`: every audio worker is
evicted and the GPU slot is held across a ~78 s Gemma load plus generation.
That is not a request; it is a queue item that stalls every other generation
while it runs. See its own docstring below.

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
    response: Response,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> JobStatusResponse:
    """
    Convert Roman Urdu into Perso-Arabic script. **202 + poll**, not synchronous.

    THE ONE THING THAT MAKES THIS DIFFERENT FROM EVERY OTHER TEXT ENDPOINT:
    the module docstring above explains at length why a short text op should be
    synchronous, and every word of it still holds — for operations that do not
    touch the GPU. This one takes the WHOLE GPU. `TransliteratorScheduler`
    runs inside `InferenceScheduler.exclusive_gpu()`, which evicts every audio
    worker and holds the GPU slot across a ~78 s model load plus generation.
    Blocking a request for that long, while stalling every other generation on
    the box, is exactly what the job queue exists for.

    `route=None`, like `ANALYZE_LLM`: this never calls `resolve()` and the
    transliterator is not in the audio catalog. Its output is editable text a
    human approves — `TransformKind` and `domain/routing.py` are untouched by
    this feature, which is what keeps golden rules 4 and 5 intact while still
    letting Roman Urdu reach OmniVoice.

    The 202 body is the same shape as `POST /api/generate`'s, built by the same
    `build_job_status_response`, so the client polls the existing generic
    `GET /api/jobs/{id}` with no new polling flow.
    """
    job = await runner.enqueue(
        JobKind.TRANSLITERATE,
        params={"text": body.text, "instruction": body.instruction},
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
