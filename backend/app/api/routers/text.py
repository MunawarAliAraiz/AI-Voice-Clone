"""
Short titles for generations.

WHY THIS IS SYNCHRONOUS
-----------------------
Every other model-backed operation here is a job (202 + poll) because it takes
tens of seconds and produces audio. This produces two words. Putting it behind
the job queue would mean a second polling flow, a second `JobKind`, and a
`jobs` row per title — all to deliver a string the client needs before it can
enqueue the thing it actually came for.

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

from fastapi import APIRouter, Request

from ...domain.direction_analyze import analyze
from ..schemas.text import TitleRequest, TitleResponse

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
