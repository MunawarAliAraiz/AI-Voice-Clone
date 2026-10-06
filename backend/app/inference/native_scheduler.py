"""Bounded Flex adapter for the existing desktop scheduler/helper contracts."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from uuid import UUID, uuid4, uuid5

import httpx

from ..exceptions import GenerationError, ModelNotFoundError
from ..remote_worker.errors import WORKER_MESSAGES
from ..runpod.flex_jobs import FlexJobError
from ..runpod.flex_protocol import MAX_AUDIO_BYTES, FlexInput, decode_audio
from ..runpod.native_admission import NativeAdmissionError
from .catalog import CATALOG
from .progress import current_request_id, emit_progress
from .protocol import SynthResult
from .remote_scheduler import RemoteWorkerError


class NativeScheduler:
    def __init__(self, backend):
        self.backend = backend

    @staticmethod
    def _operation_id(operation, suffix):
        scoped = current_request_id()
        try:
            parent = UUID(scoped) if scoped else uuid4()
        except (ValueError, TypeError, AttributeError) as exc:
            raise GenerationError("remote", "Cloud operation identity is invalid") from exc
        return uuid5(parent, operation + ":" + suffix)

    async def _execute(self, value):
        try:
            endpoint_id = await self.backend.recover_operation(value)
            recovering = endpoint_id is not None
            if endpoint_id is None:
                endpoint_id = await self.backend.start()
            await self.backend.jobs.submit_once(endpoint_id, value)
            row = self.backend.ledger.view(self.backend.session_id)
            # A saved provider ID can recover its retained result after the
            # admission deadline. This permits bounded GETs, never another /run.
            remaining = 30 if recovering else max(0, row["deadline"] - self.backend.clock())
            async with asyncio.timeout(remaining):
                while True:
                    view = await self.backend.poll_once(value.operation_id)
                    if view.stage:
                        await emit_progress(view.stage, view.model_id)
                    if view.status == "COMPLETED":
                        output = view.output
                        if output is None:
                            raise FlexJobError("The cloud result is unavailable")
                        if output.status_code >= 400:
                            code = (output.body or {}).get("code")
                            if isinstance(code, str) and code in WORKER_MESSAGES:
                                raise RemoteWorkerError(code, output.status_code)
                            raise FlexJobError(
                                "The cloud operation failed without a verified result"
                            )
                        return output
                    if view.status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                        raise FlexJobError("The cloud operation did not complete successfully")
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            # Preserve journal/ledger even if cancellation/cleanup is unavailable.
            with contextlib.suppress(FlexJobError, NativeAdmissionError, OSError, TimeoutError):
                async with asyncio.timeout(20):
                    await asyncio.shield(self.backend.request_cancel(value.operation_id))
            raise
        except RemoteWorkerError:
            raise
        except (FlexJobError, NativeAdmissionError, TimeoutError) as exc:
            if self.backend.session_id:
                self.backend.ledger.request_stop(self.backend.session_id)
                with contextlib.suppress(NativeAdmissionError, OSError):
                    await self.backend.stop()
            raise GenerationError("remote", str(exc)) from exc

    async def synthesize(self, request):
        if CATALOG.get(request.model_id) is None:
            raise ModelNotFoundError(request.model_id)
        try:
            with request.reference_audio.open("rb") as stream:
                reference = stream.read(MAX_AUDIO_BYTES + 1)
            if not reference or len(reference) > MAX_AUDIO_BYTES:
                raise ValueError
        except (OSError, ValueError) as exc:
            raise GenerationError(
                request.model_id,
                "Reference audio exceeds the qualified transport or cannot be read",
            ) from exc
        value = FlexInput(
            operation_id=self._operation_id("synthesize", request.output_path.name),
            operation="synthesize",
            payload={
                "model_id": request.model_id,
                "text": request.text,
                "reference_text": request.reference_text,
                "params": request.params,
                "sample_rate": request.sample_rate,
            },
            reference_audio_b64=base64.b64encode(reference).decode("ascii"),
        )
        output = await self._execute(value)
        audio = decode_audio(output.audio_b64)
        # FlexJobs validates operation/model, WAV and finite metadata first.
        path = request.output_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            temporary.write_bytes(audio)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return SynthResult(
            output_path=path,
            duration_sec=float(output.headers["x-audio-duration-sec"]),
            gen_time_sec=float(output.headers["x-generation-time-sec"]),
            sample_rate=request.sample_rate,
            model_id=request.model_id,
            load_time_sec=float(output.headers["x-load-time-sec"]),
        )

    async def warm(self, model_id):
        if CATALOG.get(model_id) is None:
            raise ModelNotFoundError(model_id)
        await self._execute(
            FlexInput(
                operation_id=self._operation_id("warm", model_id),
                operation="warm",
                payload={"model_id": model_id},
            )
        )

    async def _response(self, method, path, **kwargs):
        # Compatibility for RemoteFeatures. Readiness/model calls never become
        # implicit billable requests, and arbitrary paths are never forwarded.
        operation = {"/v1/analyze": "analyze", "/v1/transliterate": "transliterate"}.get(path)
        if method != "POST" or operation is None or set(kwargs) != {"json"}:
            raise GenerationError("remote", "This operation has no qualified native transport")
        payload = kwargs["json"]
        import hashlib

        suffix = hashlib.sha256(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        output = await self._execute(
            FlexInput(
                operation_id=self._operation_id(operation, suffix),
                operation=operation,
                payload=payload,
            )
        )
        return httpx.Response(output.status_code, json=output.body, headers=output.headers)

    async def shutdown(self):
        # Controller owns the shared transport and the native 60-second session.
        pass
