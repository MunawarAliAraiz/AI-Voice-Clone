"""Recoverable Flex job transport. A separately registered guard owns submission.

Development adapter only; not selected by the desktop controller. There is no
direct /run or endpoint-creation API here, and no production bypass switch.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from .flex_protocol import PROGRESS_STAGES, FlexInput, FlexOutput, decode_audio

_ID = re.compile(r"[A-Za-z0-9_-]{6,128}\Z")
_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})
_PROVIDER_STATES = _TERMINAL | {"IN_QUEUE", "IN_PROGRESS"}


class FlexJobError(RuntimeError):
    """Fixed local explanation, never a raw provider body or exception."""


class DefiniteSubmissionRejection(FlexJobError):
    """The independent guard rejected before a provider submission."""


class RegisteredSubmitter(Protocol):
    """Implementation must persist an independent alarm before any paid start.

    Submission is owned by the guard, including uncertain-response recovery.
    A returned provider ID alone is not proof of cost protection. This adapter
    cannot be enabled until that service's lifecycle is separately qualified.
    """

    async def submit_once(self, endpoint_id: str, value: FlexInput) -> str: ...


class JobReads(Protocol):
    async def status(self, endpoint_id: str, job_id: str) -> dict: ...
    async def cancel(self, endpoint_id: str, job_id: str) -> dict: ...


class QualifiedNativeSubmitter(Protocol):
    """Separate native admission boundary; requires reviewed lifecycle evidence.

    Must reserve and claim the account ledger before sending a paid request.
    It does not implement the independent-alarm RegisteredSubmitter contract.
    """

    async def submit_once(self, endpoint_id: str, value: FlexInput) -> str: ...
    async def recover_submitted(self, endpoint_id: str, value: FlexInput) -> str | None: ...


def _identity(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise FlexJobError("Invalid serverless resource identity")
    return value


class RunpodFlexJobReads:
    """Status/cancel only. Health requests can trigger workers, so none exist."""

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ValueError("Runpod API key is required")
        self._client = httpx.AsyncClient(
            base_url="https://api.runpod.ai/v2/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
            follow_redirects=False,
            transport=transport,
        )

    async def _call(self, method: str, endpoint_id: str, job_id: str) -> dict:
        endpoint_id, job_id = _identity(endpoint_id), _identity(job_id)
        operation = "status" if method == "GET" else "cancel"
        try:
            response = await self._client.request(method, f"{endpoint_id}/{operation}/{job_id}")
        except httpx.HTTPError as exc:
            raise FlexJobError(
                "Unable to check the cloud job. No generation was resubmitted."
            ) from exc
        if response.status_code == 404:
            raise FlexJobError("Cloud job result is unavailable. It may have expired.")
        if response.status_code != 200:
            raise FlexJobError("Runpod could not confirm the cloud job status.")
        try:
            value = response.json()
        except ValueError as exc:
            raise FlexJobError("Runpod returned an invalid job status.") from exc
        if not isinstance(value, dict):
            raise FlexJobError("Runpod returned an invalid job status.")
        return value

    async def status(self, endpoint_id: str, job_id: str) -> dict:
        return await self._call("GET", endpoint_id, job_id)

    async def cancel(self, endpoint_id: str, job_id: str) -> dict:
        return await self._call("POST", endpoint_id, job_id)

    async def close(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class FlexJobView:
    operation_id: str
    provider_job_id: str
    status: str
    stage: str | None = None
    model_id: str | None = None
    output: FlexOutput | None = None
    # Provider job timings are observed values, not an ETA or a bill.
    delay_ms: float | None = None
    execution_ms: float | None = None


def _timing(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _selected_model(value: FlexInput) -> str | None:
    from ..inference.analyzer_scheduler import QWEN_ANALYZER_MODEL_ID
    from ..inference.catalog import CATALOG
    from ..inference.transliterator_scheduler import GEMMA_TRANSLITERATOR_MODEL_ID

    if value.operation in {"synthesize", "warm"}:
        selected = value.payload.get("model_id")
        if not isinstance(selected, str) or CATALOG.get(selected) is None:
            raise FlexJobError("Select a supported voice model.")
        return selected
    if value.operation == "analyze":
        return QWEN_ANALYZER_MODEL_ID
    if value.operation == "transliterate":
        return GEMMA_TRANSLITERATOR_MODEL_ID
    return None


def _check_result(saved: dict, output: FlexOutput) -> None:
    if output.status_code >= 400:
        return
    selected = saved.get("selected_model")
    if saved["operation"] == "synthesize":
        try:
            if (
                output.headers.get("x-model-id") != selected
                or output.headers.get("x-request-id") != saved["operation_id"]
                or output.audio_b64 is None
            ):
                raise ValueError("Invalid identity")
            for field in ("x-audio-duration-sec", "x-generation-time-sec", "x-load-time-sec"):
                number = float(output.headers[field])
                if not math.isfinite(number) or number < 0:
                    raise ValueError("Invalid timing")
            if float(output.headers["x-audio-duration-sec"]) <= 0:
                raise ValueError("Invalid duration")
            with wave.open(io.BytesIO(decode_audio(output.audio_b64)), "rb") as audio:
                if audio.getnframes() <= 0 or audio.getnchannels() not in {1, 2}:
                    raise ValueError("Invalid WAV")
                expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                if len(audio.readframes(audio.getnframes())) != expected:
                    raise ValueError("Truncated WAV")
        except (ValueError, KeyError, wave.Error, EOFError) as exc:
            raise FlexJobError("The cloud audio result could not be verified.") from exc
    elif selected and saved["operation"] in {"analyze", "transliterate"}:
        from ..remote_worker.model_pins import AUXILIARY_PINS

        if (
            not output.body
            or output.body.get("model_id") != selected
            or output.body.get("revision") != AUXILIARY_PINS[selected][1]
        ):
            raise FlexJobError("The cloud helper changed the selected model or revision.")


class FlexJobs:
    def __init__(
        self,
        root: Path,
        reads: JobReads,
        *,
        submitter: RegisteredSubmitter | None = None,
        native_submitter: QualifiedNativeSubmitter | None = None,
    ):
        if submitter is not None and native_submitter is not None:
            raise ValueError("Choose one qualified submission owner")
        self.root, self.reads, self.submitter = root, reads, submitter
        self.native_submitter = native_submitter
        self._lock = asyncio.Lock()

    def _path(self, operation_id: str | UUID) -> Path:
        try:
            identity = UUID(str(operation_id)).hex
        except (ValueError, TypeError, AttributeError) as exc:
            raise FlexJobError("Invalid operation identity") from exc
        return self.root / f"{identity}.json"

    def _read(self, operation_id: str | UUID) -> dict:
        try:
            value = json.loads(self._path(operation_id).read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("operation_id") != str(
                UUID(str(operation_id))
            ):
                raise ValueError("Invalid journal")
            if not isinstance(value.get("fingerprint"), str):
                raise ValueError("Invalid journal")
            return value
        except (OSError, ValueError, TypeError) as exc:
            raise FlexJobError(
                "The saved cloud job needs recovery. No work was resubmitted."
            ) from exc

    def _patch(self, value: dict, **updates: Any) -> None:
        value.update(updates)
        path = self._path(value["operation_id"])
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    async def submit_once(self, endpoint_id: str, value: FlexInput) -> str:
        """Persist intent before one guard call; never replay an uncertain POST."""
        _identity(endpoint_id)
        # Serialize a validated copy even when callers used model_construct.
        try:
            value = FlexInput.model_validate(value.model_dump(mode="python"))
        except (ValidationError, ValueError, TypeError) as exc:
            raise FlexJobError("This cloud request is invalid.") from exc
        selected_model = _selected_model(value)
        fingerprint = hashlib.sha256(
            json.dumps(
                value.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        async with self._lock:
            path = self._path(value.operation_id)
            if path.exists():
                saved = await asyncio.to_thread(self._read, value.operation_id)
                if (
                    saved.get("fingerprint") != fingerprint
                    or saved.get("endpoint_id") != endpoint_id
                ):
                    raise FlexJobError("This operation identity belongs to different work.")
                if saved.get("provider_job_id"):
                    return _identity(saved["provider_job_id"])
                if self.native_submitter is not None:
                    recovered = await self.native_submitter.recover_submitted(endpoint_id, value)
                    if recovered is not None:
                        job_id = _identity(recovered)
                        await asyncio.to_thread(
                            self._patch, saved, phase="ACCEPTED", provider_job_id=job_id
                        )
                        return job_id
                raise FlexJobError("Submission is uncertain or rejected. Do not submit it again.")
            owner = self.native_submitter or self.submitter
            if owner is None:
                raise FlexJobError("Independent cloud spending protection is not connected.")
            saved = {
                "operation_id": str(value.operation_id),
                "endpoint_id": endpoint_id,
                "operation": value.operation,
                "fingerprint": fingerprint,
                "selected_model": selected_model,
                "phase": "SUBMITTING",
                "provider_job_id": None,
            }

            # Exclusive file creation fences concurrent processes/adapter instances.
            def claim():
                self.root.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(saved, stream, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())

            try:
                await asyncio.to_thread(claim)
            except FileExistsError as exc:
                raise FlexJobError("This operation is already being submitted.") from exc
            try:
                job_id = _identity(await owner.submit_once(endpoint_id, value))
            except DefiniteSubmissionRejection:
                await asyncio.to_thread(self._patch, saved, phase="REJECTED")
                raise
            except BaseException as exc:
                # Intent already exists, including if this task is cancelled or the
                # process exits before the response. Never repeat a paid submission.
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise FlexJobError("Cloud submission is uncertain. No duplicate was sent.") from exc
            await asyncio.to_thread(self._patch, saved, phase="ACCEPTED", provider_job_id=job_id)
            return job_id

    async def poll_once(self, operation_id: str | UUID) -> FlexJobView:
        saved = await asyncio.to_thread(self._read, operation_id)
        job_id = saved.get("provider_job_id")
        if not job_id:
            raise FlexJobError("Cloud submission is uncertain. Check the independent guard.")
        value = await self.reads.status(_identity(saved["endpoint_id"]), _identity(job_id))
        if value.get("id") != job_id or (
            not isinstance(value.get("status"), str) or value["status"] not in _PROVIDER_STATES
        ):
            raise FlexJobError("Runpod returned a mismatched cloud job status.")
        state = value["status"]
        output = None
        if state == "COMPLETED":
            try:
                output = FlexOutput.model_validate(value.get("output"))
            except (ValidationError, ValueError, TypeError) as exc:
                raise FlexJobError("The cloud result could not be verified.") from exc
            if str(output.operation_id) != saved["operation_id"]:
                raise FlexJobError("The cloud result belongs to another operation.")
            _check_result(saved, output)
        stage = model_id = None
        progress = value.get("progress")
        if state == "IN_PROGRESS" and isinstance(progress, dict):
            if (
                progress.get("protocol_version") == 1
                and progress.get("operation_id") == saved["operation_id"]
                and isinstance(progress.get("stage"), str)
                and progress["stage"] in PROGRESS_STAGES
            ):
                stage = progress["stage"]
                # Names must come from the app's fixed model catalog, never remote prose.
                from ..inference.catalog import CATALOG
                from ..remote_worker.model_pins import AUXILIARY_PINS

                candidate = progress.get("model_id")
                if isinstance(candidate, str) and (
                    candidate == saved.get("selected_model")
                    or (saved["operation"] == "setup" and candidate in AUXILIARY_PINS)
                    or (saved["operation"] == "setup" and CATALOG.get(candidate))
                ):
                    model_id = candidate
                else:
                    stage = None
        async with self._lock:
            # Don't downgrade a terminal journal from a late, older status read.
            current = await asyncio.to_thread(self._read, operation_id)
            if current.get("phase") not in _TERMINAL:
                await asyncio.to_thread(self._patch, current, phase=state)
        return FlexJobView(
            saved["operation_id"],
            job_id,
            state,
            stage,
            model_id,
            output,
            _timing(value.get("delayTime")),
            _timing(value.get("executionTime")),
        )

    async def cancel(self, operation_id: str | UUID, *, timeout_sec: float = 15) -> FlexJobView:
        if not math.isfinite(timeout_sec) or not 0 < timeout_sec <= 60:
            raise ValueError("Choose a bounded cancellation timeout")
        async with self._lock:
            saved = await asyncio.to_thread(self._read, operation_id)
            job_id = saved.get("provider_job_id")
            if not job_id:
                raise FlexJobError("Submission is uncertain. The guard must reconcile it.")
            terminal = saved.get("phase") in _TERMINAL
            if not terminal:
                await asyncio.to_thread(self._patch, saved, phase="CANCEL_REQUESTED")
        if terminal:
            return await self.poll_once(operation_id)
        # A cancellation acknowledgement is not resource-termination proof.
        # Poll the original job; independent guard owns actual worker cleanup.
        try:
            async with asyncio.timeout(timeout_sec):
                await self.reads.cancel(_identity(saved["endpoint_id"]), _identity(job_id))
                while True:
                    view = await self.poll_once(operation_id)
                    if view.status in _TERMINAL:
                        return view
                    await asyncio.sleep(0.25)
        except TimeoutError as exc:
            raise FlexJobError(
                "Cancellation is not confirmed. The guard must check cleanup."
            ) from exc
