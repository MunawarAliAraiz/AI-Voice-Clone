"""
AI Voice Clone Studio — the 'analyze_llm' job handler.

LLM-backed Speech Direction classification, queued behind the same job
table as synthesis. Modeled on `jobs/handlers/synthesize.py`'s shape, minus
everything audio: no `output_path`, no `generation_history` row, no
`route` — this job kind never calls `resolve()` and is never reachable
from the audio catalog (`domain/routing.py` stays untouched by this
feature entirely).

`sentences` is decided ONCE, by the router (`routers/direction.py`'s
`POST /api/direction/analyze-llm`), from a real call to the pure
`domain.direction_analyze.analyze()` — the same heuristic segmentation the
non-LLM `/direction/analyze` preview already uses. This handler MUST NOT
call `split_sentences()`/`analyze()` itself: recomputing segmentation at
claim time would be the job-queue analog of golden rule 4's bug ("a handler
calling resolve() again at claim time is rule 4's bug wearing a queue" —
golden rule 8), applied to segmentation instead of routing. The handler's
only job is handing the stored sentences to `ctx.analyzer.classify()`
verbatim and returning what came back.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..types import JobContext, JobOutcome, JobRecord

__all__ = ["AnalyzeLlmParams", "run_analyze_llm"]


class AnalyzeLlmParams(BaseModel):
    """
    Everything `run_analyze_llm` needs, captured at enqueue time by
    `POST /api/direction/analyze-llm` — never recomputed at claim time.
    Re-validated here (not just trusted), same discipline as
    `SynthesizeParams`: a row written by an older build must fail loudly,
    not be coerced.

    `sentences` is the exact `segment.text` list a real `analyze()` call
    produced for the user's text, in order — see this module's docstring.
    """

    language: str
    sentences: list[str] = Field(default_factory=list)


async def run_analyze_llm(ctx: JobContext, job: JobRecord) -> JobOutcome:
    params = AnalyzeLlmParams.model_validate(job.params)

    # Deliberately NOT `analyze(params.text, params.language)` — there is no
    # `params.text`. Segmentation already happened once, in the router; this
    # line is the entire handler body for a reason.
    result = await ctx.analyzer.classify(
        language=params.language, sentences=tuple(params.sentences)
    )

    return JobOutcome(
        result={
            "rows": list(result.rows),
            # Produced by the SAME generation as the rows, so the Composer gets
            # a title from the AI-suggest press it already made rather than
            # paying a second ~6 GB model round-trip on Generate.
            "title": result.title,
            "gen_time_sec": result.gen_time_sec,
            "load_time_sec": result.load_time_sec,
        }
    )
