"""
AI Voice Clone Studio — Exception hierarchy and RFC 9457 mapping.

CONTRACT MODULE. Wave 0. Replaces `utils/exceptions.py`, which carried codes but
no HTTP mapping, so every router invented its own status and shape.

Every error the API can produce is here, and every one knows three things:

    code         a STABLE machine-readable string. Clients branch on this.
                 Renaming one is a breaking API change.
    http_status  the status it becomes.
    to_problem() its RFC 9457 application/problem+json body.

The `{"status": "ok", "result": ...}` envelope is gone. Success bodies are the
resource; failures are problem+json. A client distinguishes them by status code,
which is what status codes are for.

Fill in `detail` with something the user can act on. "Generation failed" tells
nobody anything — it is the message the predecessor produced when ChatTTS raised
NameError, and it cost an afternoon.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "PROBLEM_CONTENT_TYPE",
    "PROBLEM_BASE_URI",
    "AppError",
    # 4xx — the caller can fix these
    "ValidationError",
    "AudioValidationError",
    "UploadTooLargeError",
    "ProfileNotFoundError",
    "HistoryNotFoundError",
    "ModelNotFoundError",
    "NoRouteError",
    "AmbiguousScriptError",
    "InvalidParamsError",
    "InvalidDirectionPlanError",
    "AuthenticationError",
    "MediaTokenError",
    # 5xx / capacity — the caller can retry
    "QueueFullError",
    "VRAMExhaustedError",
    "ModelLoadError",
    "ModelNotDownloadedError",
    "WorkerCrashedError",
    "GenerationTimeoutError",
    "GenerationError",
    "AnalyzerUnavailableError",
    "AnalyzerResponseInvalidError",
    # Jobs (async queue)
    "JobNotFoundError",
    "JobQueueFullError",
    "JobNotCancellableError",
    "JobInterruptedError",
    "JobExpiredError",
]

PROBLEM_CONTENT_TYPE = "application/problem+json"

#: `type` URIs are namespaced under this. They need not resolve, but they must be
#: stable — they are the documentation anchor for each code.
PROBLEM_BASE_URI = "https://voiceclone.local/problems"


class AppError(Exception):
    """
    Base for everything this application raises deliberately.

    A single handler registered on the app converts any AppError into
    problem+json. Anything that is NOT an AppError escaping to that handler is a
    bug, and is logged with a traceback and returned as a bare 500 with no
    detail — internal messages must never reach a client.
    """

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    title: str = "Internal error"

    def __init__(self, detail: str, **extensions: Any) -> None:
        self.detail = detail
        #: Extra members merged into the problem document. RFC 9457 allows
        #: arbitrary extension members, and this is where actionable data goes —
        #: e.g. NoRouteError's list of pairs that WOULD work.
        self.extensions = extensions
        super().__init__(detail)

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        """Serialize to an RFC 9457 problem document."""
        problem: dict[str, Any] = {
            "type": f"{PROBLEM_BASE_URI}/{self.code.lower().replace('_', '-')}",
            "title": self.title,
            "status": self.http_status,
            "detail": self.detail,
            "code": self.code,
        }
        if instance:
            problem["instance"] = instance
        problem.update(self.extensions)
        return problem


# ── Validation (400 / 413) ───────────────────────────────────────────────────


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    http_status = 400
    title = "Invalid request"


class AudioValidationError(ValidationError):
    code = "AUDIO_VALIDATION_ERROR"
    title = "Invalid audio file"

    def __init__(self, detail: str, **extensions: Any) -> None:
        super().__init__(detail, **extensions)


class UploadTooLargeError(AppError):
    code = "UPLOAD_TOO_LARGE"
    http_status = 413
    title = "Upload too large"

    def __init__(self, limit_mb: int, actual_mb: float | None = None) -> None:
        detail = f"Reference audio must be under {limit_mb} MB."
        if actual_mb is not None:
            detail = f"Reference audio is {actual_mb:.1f} MB; the limit is {limit_mb} MB."
        super().__init__(detail, limit_mb=limit_mb, actual_mb=actual_mb)


# ── Not found (404) ──────────────────────────────────────────────────────────


class ProfileNotFoundError(AppError):
    code = "PROFILE_NOT_FOUND"
    http_status = 404
    title = "Voice profile not found"

    def __init__(self, profile_id: int) -> None:
        super().__init__(f"Voice profile {profile_id} does not exist.", profile_id=profile_id)


class HistoryNotFoundError(AppError):
    code = "HISTORY_NOT_FOUND"
    http_status = 404
    title = "Generation not found"

    def __init__(self, history_id: int) -> None:
        super().__init__(f"Generation {history_id} does not exist.", history_id=history_id)


class PronunciationNotFoundError(AppError):
    code = "PRONUNCIATION_NOT_FOUND"
    http_status = 404
    title = "Dictionary entry not found"

    def __init__(self, entry_id: int) -> None:
        super().__init__(
            f"Pronunciation entry {entry_id} does not exist.", entry_id=entry_id
        )


class PronunciationConflictError(AppError):
    """
    A dictionary entry already exists for this word in this language.

    Carries the existing entry's id as an extension member so the client can
    offer "edit the one you already have" instead of only reporting a clash —
    a user re-adding `database` almost always means they forgot, not that they
    want a second row that could never win the match anyway.
    """

    code = "PRONUNCIATION_CONFLICT"
    http_status = 409
    title = "Word already in the dictionary"

    def __init__(self, key_text: str, language: str, existing_id: int | None = None) -> None:
        super().__init__(
            f"{key_text!r} already has a pronunciation entry for language "
            f"{language!r}. Matching is case-insensitive, so a different "
            f"capitalisation is the same entry.",
            key_text=key_text, language=language, existing_id=existing_id,
        )


class ModelNotFoundError(AppError):
    code = "MODEL_NOT_FOUND"
    http_status = 404
    title = "Unknown model"

    def __init__(self, model_id: str, available: tuple[str, ...] = ()) -> None:
        super().__init__(
            f"No model with id {model_id!r} is registered.",
            model_id=model_id,
            available=list(available),
        )


# ── Routing (422) ────────────────────────────────────────────────────────────


class NoRouteError(AppError):
    """
    Nothing in the catalog can serve this (language, script).

    THE most important error in the system. The entire point of the rewrite is
    that this is raised instead of silently substituting whatever happened to be
    loaded. The body must enumerate what WOULD work — a bare "unsupported" sends
    the user guessing, which is barely better than the sine wave.
    """

    code = "NO_ROUTE"
    http_status = 422
    title = "No model can render this text"

    def __init__(
        self,
        language: str,
        script: str,
        supported: tuple[tuple[str, str], ...] = (),
        suggestion: str | None = None,
    ) -> None:
        detail = f"No available model renders {language!r} written in {script} script."
        if suggestion:
            detail = f"{detail} {suggestion}"
        super().__init__(
            detail,
            language=language,
            script=script,
            supported=[{"language": lang, "script": scr} for lang, scr in supported],
            suggestion=suggestion,
        )


class AmbiguousScriptError(AppError):
    """Text mixes scripts substantially. Guessing produces audibly broken output."""

    code = "AMBIGUOUS_SCRIPT"
    http_status = 422
    title = "Mixed scripts in input"

    def __init__(self, ratios: dict[str, float]) -> None:
        mix = ", ".join(f"{s} {r:.0%}" for s, r in sorted(ratios.items(), key=lambda kv: -kv[1]))
        super().__init__(
            f"The text mixes scripts ({mix}). Split it into separate requests, "
            f"one per script.",
            script_ratios=ratios,
        )


class InvalidParamsError(AppError):
    """A generation parameter is not declared by the selected model."""

    code = "INVALID_PARAMS"
    http_status = 422
    title = "Invalid generation parameters"

    def __init__(self, model_id: str, unknown: tuple[str, ...], accepted: tuple[str, ...]) -> None:
        super().__init__(
            f"Model {model_id!r} does not accept {', '.join(sorted(unknown))}.",
            model_id=model_id,
            unknown=sorted(unknown),
            accepted=sorted(accepted),
        )


class InvalidDirectionPlanError(AppError):
    """
    `direction_plan` overrides a segment index that a fresh analyze() of
    `body.text` doesn't have — most often the text was edited after the
    Advanced editor fetched its plan, so the indices no longer line up.

    422, not a silent "ignore the stale override": the same no-silent-fallback
    discipline `NoRouteError`/`InvalidParamsError` already follow.
    """

    code = "INVALID_DIRECTION_PLAN"
    http_status = 422
    title = "Invalid direction plan"

    def __init__(self, unknown_indices: tuple[int, ...], valid_indices: tuple[int, ...]) -> None:
        super().__init__(
            f"direction_plan overrides segment index(es) "
            f"{', '.join(str(i) for i in sorted(unknown_indices))}, but analyzing this text "
            f"produced segments {list(valid_indices)}. The text likely changed since the plan "
            f"was fetched — re-run GET /api/direction/analyze and retry.",
            unknown_indices=sorted(unknown_indices),
            valid_indices=list(valid_indices),
        )


# ── Auth (401 / 403) ─────────────────────────────────────────────────────────


class AuthenticationError(AppError):
    code = "UNAUTHORIZED"
    http_status = 401
    title = "Authentication required"

    def __init__(self, detail: str = "A valid X-API-Key header is required.") -> None:
        super().__init__(detail)


class MediaTokenError(AppError):
    """
    A signed media URL is missing, malformed, or expired.

    Media tokens exist because `<audio>` elements cannot send auth headers. The
    signature is checked with `hmac.compare_digest`, and this error deliberately
    does not distinguish "bad signature" from "expired" — telling an attacker
    which half they got right is free information.
    """

    code = "INVALID_MEDIA_TOKEN"
    http_status = 403
    title = "Invalid or expired media link"

    def __init__(self, detail: str = "This media link is invalid or has expired.") -> None:
        super().__init__(detail)


# ── Capacity and runtime (5xx) ───────────────────────────────────────────────


class QueueFullError(AppError):
    """
    Admission bound reached.

    A bounded queue that rejects with 503 beats an unbounded one that OOMs the
    box. `retry_after_sec` is advisory and belongs in a Retry-After header too.
    """

    code = "QUEUE_FULL"
    http_status = 503
    title = "Server busy"

    def __init__(self, limit: int, retry_after_sec: int = 10) -> None:
        super().__init__(
            f"All {limit} inference slots are in use. Retry in a few seconds.",
            limit=limit,
            retry_after_sec=retry_after_sec,
        )


class VRAMExhaustedError(AppError):
    """The model does not fit even after evicting everything else."""

    code = "VRAM_EXHAUSTED"
    http_status = 503
    title = "Insufficient GPU memory"

    def __init__(self, model_id: str, required_mb: int, budget_mb: int) -> None:
        super().__init__(
            f"Model {model_id!r} needs ~{required_mb} MB but the budget is {budget_mb} MB.",
            model_id=model_id,
            required_mb=required_mb,
            budget_mb=budget_mb,
        )


class ModelLoadError(AppError):
    code = "MODEL_LOAD_FAILED"
    http_status = 500
    title = "Model failed to load"

    def __init__(self, model_id: str, detail: str) -> None:
        super().__init__(f"Could not load {model_id!r}: {detail}", model_id=model_id)


class ModelNotDownloadedError(AppError):
    code = "MODEL_NOT_DOWNLOADED"
    http_status = 409
    title = "Model weights not downloaded"

    def __init__(self, model_id: str, size_mb: int | None = None) -> None:
        super().__init__(
            f"Weights for {model_id!r} are not on disk. Download them first.",
            model_id=model_id,
            size_mb=size_mb,
        )


class WorkerCrashedError(AppError):
    """The worker process died mid-request — usually CUDA OOM or an illegal access."""

    code = "WORKER_CRASHED"
    http_status = 500
    title = "Inference worker crashed"

    def __init__(self, model_id: str, exit_code: int | None = None) -> None:
        super().__init__(
            f"The worker running {model_id!r} exited unexpectedly.",
            model_id=model_id,
            exit_code=exit_code,
        )


class GenerationTimeoutError(AppError):
    """
    Exceeded its deadline; the worker was killed.

    SIGKILL rather than cancellation is deliberate: a wedged CUDA kernel cannot
    be interrupted, so the process is the only unit that can be reclaimed.
    """

    code = "GENERATION_TIMEOUT"
    http_status = 504
    title = "Generation timed out"

    def __init__(self, model_id: str, timeout_sec: float) -> None:
        super().__init__(
            f"{model_id!r} exceeded its {timeout_sec:.0f}s budget and was terminated.",
            model_id=model_id,
            timeout_sec=timeout_sec,
        )


class GenerationError(AppError):
    """Synthesis failed for a reason the worker could describe."""

    code = "GENERATION_FAILED"
    http_status = 500
    title = "Generation failed"

    def __init__(self, model_id: str, detail: str) -> None:
        super().__init__(detail, model_id=model_id)


class AnalyzerUnavailableError(AppError):
    """
    The Qwen Speech-Direction analyzer worker could not be started or died
    before/during a request — no interpreter configured
    (`Settings.qwen_analyzer_python` unset), the subprocess failed its READY
    handshake, or it crashed mid-classify. 503, not 500: this is an
    infrastructure failure a retry might just clear, distinct from
    `AnalyzerResponseInvalidError` below, where the model itself violated
    the contract and a bare retry would likely reproduce it.
    """

    code = "ANALYZER_UNAVAILABLE"
    http_status = 503
    title = "Speech-direction analyzer unavailable"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class AnalyzerResponseInvalidError(AppError):
    """
    The LLM analyzer's response failed validation: not JSON, not a list, the
    wrong length, or an `emotion`/`intensity`/`energy`/`rate` value outside
    `Emotion`/`Level`/`Rate`. Golden rule 5 (no silent fallback) applies here
    exactly as it does to routing — `QwenAnalyzerBackend.classify()` raises
    rather than substituting a default classification, and this is what that
    raise becomes once it crosses the wire protocol back to the scheduler.
    """

    code = "ANALYZER_RESPONSE_INVALID"
    http_status = 502
    title = "Speech-direction analyzer returned an invalid response"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


# ── Jobs (async queue) ───────────────────────────────────────────────────────
#
# The job queue moves synthesis off the request/response cycle: POST /generate
# writes a row and returns 202, a pool claims it, and the client polls. These
# five errors are distinct from the scheduler's own QueueFullError/GenerationTimeoutError
# above (raised directly by a blocking synthesize() call) — a job error is raised
# by the enqueue/poll/cancel surface, or stored on a job row by the runner. Two
# (JobInterruptedError, JobExpiredError) are never raised into a live response;
# they exist so the startup reaper can store a real RFC 9457 code, status, title,
# and detail on a row, so the polling endpoint renders the identical problem+json
# shape whether the failure is live or reaped.


class JobNotFoundError(AppError):
    code = "JOB_NOT_FOUND"
    http_status = 404
    title = "Job not found"

    def __init__(self, job_id: int) -> None:
        super().__init__(f"Job {job_id} does not exist.", job_id=job_id)


class JobQueueFullError(AppError):
    """
    Queue depth bound reached.

    Distinct from QueueFullError: that one means "the GPU is momentarily
    saturated, retry in seconds" (a scheduler admission bound). This one means
    "you already have `limit` jobs pending" — a much larger, durable bound on
    the durable jobs table, not the in-memory admission semaphore.
    """

    code = "JOB_QUEUE_FULL"
    http_status = 503
    title = "Job queue full"

    def __init__(self, limit: int, depth: int, retry_after_sec: int = 30) -> None:
        super().__init__(
            f"You already have {depth} jobs pending; the limit is {limit}.",
            limit=limit,
            depth=depth,
            retry_after_sec=retry_after_sec,
        )


class TransliteratorUnavailableError(AppError):
    """
    The Roman/Devanagari → Perso-Arabic transliterator could not run.

    Infrastructure, not output quality: no interpreter configured, the worker
    failed to start, or it died mid-request. A model that ran and produced
    something unusable is `TransliterationRejectedError` — the distinction
    matters because one is "retry" and the other is "edit your text".
    """

    code = "TRANSLITERATOR_UNAVAILABLE"
    http_status = 503
    title = "Transliterator unavailable"


class TransliterationRejectedError(AppError):
    """
    The model ran, and what came back is not a transliteration.

    Carries `reason` from `domain/transliterate.py` so a client can tell an
    echo from an answer from a summary without parsing prose.
    """

    code = "TRANSLITERATION_REJECTED"
    http_status = 422
    title = "Not a transliteration"

    def __init__(self, detail: str, reason: str) -> None:
        super().__init__(detail, reason=reason)


class UnsupportedConversionError(AppError):
    """
    No prompt exists for this (source, target) pair.

    A 422 at ENQUEUE, deliberately, not a failed job. A conversion the
    transliterator has no exemplars for is a bad request — the client asked for
    something that does not exist — and finding that out forty seconds into a
    19 GB model load would spend the whole GPU to report a typo. The pairs are
    `domain/transliterate.SUPPORTED_PAIRS`.

    Both scripts ride in the payload because "unsupported" alone does not tell
    a caller whether they named a bad target or handed over text in an
    unexpected script.
    """

    code = "UNSUPPORTED_CONVERSION"
    http_status = 422
    title = "Conversion not supported"

    def __init__(self, source: str, target: str) -> None:
        super().__init__(
            f"Cannot convert {source} to {target}.",
            source_script=source,
            target_script=target,
        )


class JobNotRetryableError(AppError):
    """
    Retry is for jobs that are DONE and went wrong. A queued or running job is
    not stuck, it is working — offering to duplicate it would put two
    generations of the same text on a GPU that runs one at a time.
    """

    code = "JOB_NOT_RETRYABLE"
    http_status = 409
    title = "Job cannot be retried"

    def __init__(self, job_id: int, status: str) -> None:
        super().__init__(
            f"Job {job_id} is {status!r}; only a finished job can be retried.",
            job_id=job_id,
            status=status,
        )


class JobNotCancellableError(AppError):
    code = "JOB_NOT_CANCELLABLE"
    http_status = 409
    title = "Job cannot be cancelled"

    def __init__(self, job_id: int, status: str) -> None:
        super().__init__(
            f"Job {job_id} is {status!r} and cannot be cancelled.",
            job_id=job_id,
            status=status,
        )


class JobInterruptedError(AppError):
    """
    Stored only. Written by the startup reaper for a `running` row found at
    boot — dead by definition, since this app runs one uvicorn worker and the
    only process that could have been running it just started.
    """

    code = "JOB_INTERRUPTED"
    http_status = 503
    title = "Job interrupted"

    def __init__(self) -> None:
        super().__init__("The server restarted while this job was running. Re-submit it.")


class JobExpiredError(AppError):
    """Stored only. A `queued` job older than `job_queue_max_age_sec` at boot."""

    code = "JOB_EXPIRED"
    http_status = 503
    title = "Job expired"

    def __init__(self) -> None:
        super().__init__("This job sat queued too long across a server restart. Re-submit it.")
