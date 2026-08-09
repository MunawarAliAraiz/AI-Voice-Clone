"""
AI Voice Clone Studio — the 'synthesize' job handler.

This is the body that used to live inline in `POST /api/generate`
(`api/routers/tts.py`) between routing and the response — moved here
verbatim in effect, just re-homed behind the job queue. Routing itself
(`resolve()`) still runs exactly once, in the router, at enqueue time; this
handler never calls it. It reads the `RoutePlan` the router already decided
from `job.route` and fails loudly (`ModelNotFoundError`) if the model that
was routed to has since left the catalog — it does not silently re-route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...audio import apply_audio_effects
from ...exceptions import GenerationError, ModelNotFoundError
from ...inference.protocol import SynthRequest
from ...inference.spec import RuntimeKind
from ..types import JobContext, JobOutcome, JobRecord

__all__ = ["SynthesizeParams", "run_synthesize"]


class SynthesizeParams(BaseModel):
    """
    Everything `run_synthesize` needs, captured at enqueue time by the router
    — never recomputed at claim time. Re-validated here (not just trusted)
    because a row written by an older build must fail loudly, not be coerced.
    """

    model_config = ConfigDict(protected_namespaces=())

    #: Post-transform text the model actually receives (== RoutePlan.resolved_text).
    text: str
    #: Pre-transform text, for the generation_history row.
    input_text: str
    language: str
    reference_audio: str
    reference_text: str | None = None
    output_path: str
    output_format: str = "wav"
    sample_rate: int = 44_100
    #: Model-specific knobs, already validated against the spec's declared
    #: params by the router (`_validate_params`) before the job was created.
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)
    #: ffmpeg tempo adjustment applied to the finished file. Real DSP (pitch-
    #: preserving, sample-rate independent) — not model conditioning; see
    #: `app/audio.py` for why "emotion"/"style exaggeration" were removed
    #: rather than kept alongside this.
    speed: float = 1.0


async def run_synthesize(ctx: JobContext, job: JobRecord) -> JobOutcome:
    params = SynthesizeParams.model_validate(job.params)
    route = job.route or {}
    model_id = route.get("model_id")
    if not model_id:
        raise GenerationError("unknown", "Job has no route; this is a bug in the enqueue path.")

    spec = ctx.catalog.get(model_id)
    if spec is None:
        # The catalog changed between enqueue and claim (a model was removed).
        # Fail loudly — re-routing here would be routing-by-residency, the
        # defect this codebase exists to prevent. The user re-submits.
        raise ModelNotFoundError(model_id)

    result = await ctx.scheduler.synthesize(
        SynthRequest(
            model_id=model_id,
            text=params.text,
            reference_audio=Path(params.reference_audio),
            output_path=Path(params.output_path),
            reference_text=params.reference_text,
            params=params.params,
            sample_rate=params.sample_rate,
        )
    )

    apply_audio_effects(result.output_path, speed=params.speed)

    row = await ctx.db.create_generation(
        profile_id=job.profile_id,
        input_text=params.input_text,
        language=params.language,
        output_path=str(result.output_path),
        output_format=params.output_format,
        duration_sec=result.duration_sec,
        gen_time_sec=result.gen_time_sec,
        model_id=model_id,
        transform=str(route.get("transform", "none")),
        is_lossy=bool(route.get("lossy", False)),
        source_script=str(route.get("source_script", "unknown")),
        route_rationale=str(route.get("rationale", "")),
        resolved_text=params.text,
    )

    # Golden rule 1: fake audio is never silent about being fake. The old
    # synchronous endpoint set X-Fake-Audio on the response directly; here the
    # response is built later by the polling endpoint, from this stored flag
    # — see routers/jobs.py, which sets both the header and `is_fake` in the
    # body (a Cloudflare Worker proxies /api/*, and proxies can strip headers).
    result_payload: dict[str, Any] = {
        "history_id": row["id"],
        "duration_sec": result.duration_sec,
        "gen_time_sec": result.gen_time_sec,
        "rtf": result.rtf,
        "language": params.language,
        "created_at": row["created_at"],
        "is_fake": spec.runtime is RuntimeKind.FAKE,
    }
    return JobOutcome(history_id=row["id"], result=result_payload)
