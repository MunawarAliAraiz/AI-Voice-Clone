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

from ...audio import apply_audio_effects, concat_wavs_with_pauses
from ...exceptions import GenerationError, ModelNotFoundError
from ...inference.protocol import SynthRequest
from ...inference.spec import RuntimeKind
from ..types import JobContext, JobOutcome, JobRecord

__all__ = ["DirectedSegmentParams", "SynthesizeParams", "run_synthesize"]


class DirectedSegmentParams(BaseModel):
    """
    One rendered segment for directed (multi-segment) generation, produced by
    `jobs.direction.render()` at enqueue and stored on the job. `params` and
    `speed` already fold together the user's base knobs and the segment's own
    direction — the handler synthesizes this verbatim, it does not re-derive.
    """

    index: int
    text: str
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)
    #: Combined tempo (user speed x segment rate), pre-clamped to [0.5, 2.0].
    speed: float = 1.0
    #: Silence to insert AFTER this segment, milliseconds.
    pause_after_ms: int = 0


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
    #: Short human label. Optional and defaulted rather than required, because
    #: a job row written by an older build has no `title` key and must still
    #: decode — the same reason `segments` is optional above.
    title: str | None = None
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
    #: When present, generate each segment separately (its own text/params/speed)
    #: and join with inter-segment pauses — Speech Direction made audible. None
    #: means the original single-shot path. A row written by an older build has
    #: no `segments` key, so it decodes to None and still runs single-shot.
    segments: list[DirectedSegmentParams] | None = None


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

    if params.segments:
        out_path, duration_sec, gen_time_sec = await _synthesize_directed(ctx, params, model_id)
    else:
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
        out_path = result.output_path
        duration_sec = result.duration_sec
        gen_time_sec = result.gen_time_sec

    rtf = round(gen_time_sec / duration_sec, 4) if duration_sec > 0 else None

    row = await ctx.db.create_generation(
        profile_id=job.profile_id,
        input_text=params.input_text,
        language=params.language,
        output_path=str(out_path),
        output_format=params.output_format,
        duration_sec=duration_sec,
        gen_time_sec=gen_time_sec,
        model_id=model_id,
        transform=str(route.get("transform", "none")),
        is_lossy=bool(route.get("lossy", False)),
        source_script=str(route.get("source_script", "unknown")),
        route_rationale=str(route.get("rationale", "")),
        resolved_text=params.text,
        title=params.title,
    )

    # Golden rule 1: fake audio is never silent about being fake. The old
    # synchronous endpoint set X-Fake-Audio on the response directly; here the
    # response is built later by the polling endpoint, from this stored flag
    # — see routers/jobs.py, which sets both the header and `is_fake` in the
    # body (a Cloudflare Worker proxies /api/*, and proxies can strip headers).
    result_payload: dict[str, Any] = {
        "history_id": row["id"],
        "duration_sec": duration_sec,
        "gen_time_sec": gen_time_sec,
        "rtf": rtf,
        "language": params.language,
        "created_at": row["created_at"],
        "is_fake": spec.runtime is RuntimeKind.FAKE,
        "segment_count": len(params.segments) if params.segments else 1,
    }
    return JobOutcome(history_id=row["id"], result=result_payload)


async def _synthesize_directed(
    ctx: JobContext, params: SynthesizeParams, model_id: str
) -> tuple[Path, float, float]:
    """
    Synthesize each segment to its own temp WAV (same warm model, one scheduler
    call each), apply the segment's tempo, then join with inter-segment pauses
    into `params.output_path`. Returns (output_path, duration_sec, gen_time_sec).

    The final file's path was recorded on the job row at enqueue (the orphan
    rule), so a crash mid-join leaves a tracked path, never an orphan. Temp
    segment files are always cleaned up. gen_time is the sum across segments;
    duration is measured from the joined file.
    """
    assert params.segments is not None
    out_path = Path(params.output_path)
    reference_audio = Path(params.reference_audio)

    parts: list[Path] = []
    pauses: list[int] = []
    total_gen = 0.0
    try:
        for seg in params.segments:
            part = out_path.with_name(f"{out_path.stem}.seg{seg.index}.wav")
            result = await ctx.scheduler.synthesize(
                SynthRequest(
                    model_id=model_id,
                    text=seg.text,
                    reference_audio=reference_audio,
                    output_path=part,
                    reference_text=params.reference_text,
                    params=seg.params,
                    sample_rate=params.sample_rate,
                )
            )
            apply_audio_effects(result.output_path, speed=seg.speed)
            parts.append(result.output_path)
            pauses.append(seg.pause_after_ms)
            total_gen += result.gen_time_sec

        duration = concat_wavs_with_pauses(parts, pauses, out_path)
    finally:
        for part in parts:
            part.unlink(missing_ok=True)

    return out_path, duration, total_gen
