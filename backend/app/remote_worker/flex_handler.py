"""Offline-qualified in-process bridge. This module does not rent compute.

The provider SDK may call handle() on one persistent handler. It must close the
handler on worker shutdown; no HTTP listener or provider credentials are used.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import secrets
import wave
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from ..inference.analyzer_scheduler import QWEN_ANALYZER_MODEL_ID
from ..inference.catalog import CATALOG
from ..inference.transliterator_scheduler import GEMMA_TRANSLITERATOR_MODEL_ID
from ..runpod.flex_protocol import (
    MAX_AUDIO_BYTES,
    PROGRESS_STAGES,
    RESPONSE_HEADERS,
    FlexInput,
    FlexOutput,
    decode_audio,
)
from .errors import WORKER_MESSAGES
from .main import (
    RemoteAnalyzeRequest,
    RemoteSynthRequest,
    RemoteTransliterateRequest,
    create_worker_app,
)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
_ZERO_ID = UUID(int=0)
_MESSAGES = {
    "INVALID_INPUT": "This cloud request is invalid.",
    "OPERATION_CONFLICT": "This operation ID was already used for different work.",
    "PRIOR_RESULT_UNAVAILABLE": (
        "This operation was already admitted. Its previous result is unavailable; "
        "it will not run again."
    ),
    "MARKER_UNAVAILABLE": "The cloud operation record could not be saved. No work was started.",
    "INVALID_RESULT": "The cloud worker returned an invalid result.",
    "REQUEST_REJECTED": "The cloud worker rejected this request.",
    "SETUP_FAILED": "Some required models could not be prepared.",
    "INTERNAL_ERROR": WORKER_MESSAGES["INTERNAL_ERROR"],
}
_INSTALL_FIELDS = {
    "state", "revision", "bytes_total", "bytes_completed", "files_total",
    "files_verified", "progress_pct", "alias_of",
}


def _error(identity: UUID, status: int, code: str) -> dict[str, Any]:
    return FlexOutput(
        operation_id=identity, status_code=status,
        body={"code": code, "detail": _MESSAGES.get(code, WORKER_MESSAGES.get(
            code, WORKER_MESSAGES["INTERNAL_ERROR"],
        ))},
    ).model_dump(mode="json", exclude_none=True)


def _validated_payload(envelope: FlexInput) -> dict[str, Any]:
    payload = envelope.payload
    schemas = {
        "synthesize": RemoteSynthRequest,
        "analyze": RemoteAnalyzeRequest,
        "transliterate": RemoteTransliterateRequest,
    }
    schema = schemas.get(envelope.operation)
    if schema is not None:
        if set(payload) - set(schema.model_fields):
            raise ValueError("Unexpected payload field")
        result = schema.model_validate(payload, strict=True).model_dump()
        if envelope.operation == "synthesize":
            spec = CATALOG.get(result["model_id"])
            if spec is None or set(result["params"]) - set(spec.params):
                raise ValueError("Unknown model or parameter")
            # Parameters are scalar generation knobs, never file/URL inputs.
            for key, value in result["params"].items():
                definition = spec.params[key]
                numeric = definition.get("type") in {"integer", "number"}
                if numeric:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError("Invalid generation parameter")
                    if definition["type"] == "integer" and not isinstance(value, int):
                        raise ValueError("Invalid generation parameter")
                    if not math.isfinite(value):
                        raise ValueError("Invalid generation parameter")
                    if value < definition.get("minimum", -math.inf):
                        raise ValueError("Invalid generation parameter")
                    if value > definition.get("maximum", math.inf):
                        raise ValueError("Invalid generation parameter")
                elif definition.get("enum") and value not in definition["enum"]:
                    raise ValueError("Invalid generation parameter")
                elif not isinstance(value, (str, bool, int, float)):
                    raise ValueError("Invalid generation parameter")
            if not result["text"].strip() or len(result["text"]) > 30_000:
                raise ValueError("Invalid script")
        return result
    if envelope.operation == "warm":
        if set(payload) != {"model_id"} or CATALOG.get(payload["model_id"]) is None:
            raise ValueError("Unknown warm model")
        return payload
    if payload:
        raise ValueError("This operation does not accept a payload")
    return {}


def _model_id(envelope: FlexInput) -> str | None:
    if envelope.operation in {"synthesize", "warm"}:
        return envelope.payload["model_id"]
    if envelope.operation == "analyze":
        return QWEN_ANALYZER_MODEL_ID
    if envelope.operation == "transliterate":
        return GEMMA_TRANSLITERATOR_MODEL_ID
    return None


def _clean_install(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key in _INSTALL_FIELDS}


class FlexHandler:
    """Serial, persistent worker app with once-only admission on a mounted volume.

    volume_namespace must be the trusted, dedicated app directory on a durable
    volume. Never pass a user/provider payload path. O_EXCL semantics on Runpod's
    network volume still require live qualification before production.
    """

    def __init__(
        self, *, volume_namespace: Path, worker_app=None,
        progress_callback: ProgressCallback | None = None, poll_interval: float = 0.2,
        operation_timeout_sec: float = 600,
        **worker_kwargs,
    ) -> None:
        self._token = secrets.token_urlsafe(32)
        if worker_app is not None:
            raise ValueError("Inject scheduler/helpers, not an unauthenticated worker app")
        self.app = create_worker_app(token=self._token, **worker_kwargs)
        self.marker_dir = Path(volume_namespace) / "flex-operations-v1"
        self._callback = progress_callback
        self._poll_interval = max(0.005, poll_interval)
        if not 0 < operation_timeout_sec <= 3600:
            raise ValueError("Invalid operation timeout")
        self._operation_timeout = operation_timeout_sec
        self._dispatch_lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Handler is closed")
        if self._client is not None:
            return
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(self.app.router.lifespan_context(self.app))
            client = await stack.enter_async_context(httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                base_url="http://in-process-worker",
                timeout=None,  # noqa: S113 - no socket; dispatch is bounded by asyncio.timeout.
                headers={"Authorization": f"Bearer {self._token}"},
            ))
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._client = stack, client

    async def close(self) -> None:
        async with self._dispatch_lock:
            self._closed = True
            if self._stack is not None:
                await self._stack.aclose()
            self._stack = self._client = None

    async def __aenter__(self):
        async with self._dispatch_lock:
            await self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    def _claim(self, envelope: FlexInput) -> tuple[Path | None, str | None]:
        fingerprint = hashlib.sha256(json.dumps(
            envelope.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode()).hexdigest()
        self.marker_dir.mkdir(parents=True, exist_ok=True)
        if self.marker_dir.is_symlink():
            raise OSError("Invalid marker directory")
        path = self.marker_dir / f"{envelope.operation_id}.json"
        record = {
            "operation": envelope.operation, "fingerprint": fingerprint, "status": "admitted",
        }
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # A malformed/unreadable prior marker remains a no-replay fence.
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None, "PRIOR_RESULT_UNAVAILABLE"
            if not isinstance(prior, dict):
                return None, "PRIOR_RESULT_UNAVAILABLE"
            if prior.get("fingerprint") != fingerprint:
                return None, "OPERATION_CONFLICT"
            return None, "PRIOR_RESULT_UNAVAILABLE"
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        return path, None

    @staticmethod
    def _finish(path: Path, status: str) -> None:
        # Updating this record is diagnostic only: even a crashed or truncated
        # marker permanently fences the admitted ID. No user data is persisted.
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["status"] = status
            with path.open("w", encoding="utf-8") as stream:
                json.dump(record, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, ValueError):
            pass

    async def handle(
        self, job: dict[str, Any], progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        identity = _ZERO_ID
        try:
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                raise ValueError("Invalid provider job")
            raw = job["input"]
            if isinstance(raw, dict):
                with contextlib.suppress(ValueError, TypeError, AttributeError):
                    identity = UUID(str(raw.get("operation_id")))
            envelope = FlexInput.model_validate(raw)
            identity = envelope.operation_id
            envelope.payload = _validated_payload(envelope)
        except (ValueError, TypeError, KeyError, ValidationError):
            return _error(identity, 422, "INVALID_INPUT")
        try:
            path, conflict = await asyncio.to_thread(self._claim, envelope)
        except OSError:
            return _error(identity, 503, "MARKER_UNAVAILABLE")
        if conflict is not None:
            return _error(identity, 409, conflict)
        assert path is not None
        terminal = "failed"
        monitor = None
        callback = progress_callback or self._callback
        try:
            async with self._dispatch_lock:
                await self.start()
                if callback is not None:
                    monitor = asyncio.create_task(self._monitor(envelope, callback))
                try:
                    async with asyncio.timeout(self._operation_timeout):
                        result = await self._dispatch(envelope)
                except BaseException:
                    # Only cancel work started by this dispatch, while its serial
                    # slot is still held. A cancelled queued job owns no transfers.
                    if envelope.operation == "setup":
                        await self.app.state.installer.shutdown()
                    raise
                terminal = "completed" if result["status_code"] < 400 else "failed"
                return result
        except asyncio.CancelledError:
            terminal = "interrupted"
            raise
        except TimeoutError:
            terminal = "interrupted"
            return _error(identity, 504, "GENERATION_TIMEOUT")
        except Exception:
            return _error(identity, 500, "INTERNAL_ERROR")
        finally:
            if monitor is not None:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            await asyncio.to_thread(self._finish, path, terminal)

    async def _monitor(self, envelope: FlexInput, callback: ProgressCallback) -> None:
        selected = _model_id(envelope)
        last = None
        while True:
            events = [
                row for row in self.app.state.activity.snapshot()
                if row["request_id"] == str(envelope.operation_id)
                and row["model_id"] == selected and row["stage"] in PROGRESS_STAGES
            ]
            if envelope.operation == "setup":
                events = [{
                    "model_id": model_id,
                    "stage": "checking_files" if row["state"] == "discovering" else row["state"],
                } for model_id, row in self.app.state.installer.setup_status()["models"].items()
                    if row["state"] in {"discovering", "downloading", "verifying"}]
            state = tuple((row["model_id"], row["stage"]) for row in events)
            if state != last:
                for row in events:
                    try:
                        await callback({
                            "protocol_version": 1,
                            "operation_id": str(envelope.operation_id),
                            "stage": row["stage"], "model_id": row["model_id"],
                        })
                    except Exception:
                        # Telemetry failure must not trigger duplicate inference.
                        return
                last = state
            await asyncio.sleep(self._poll_interval)

    async def _dispatch(self, envelope: FlexInput) -> dict[str, Any]:
        assert self._client is not None
        headers = {"X-Request-Id": str(envelope.operation_id)}
        op = envelope.operation
        if op == "synthesize":
            response = await self._client.post(
                "/v1/synthesize", headers=headers,
                data={"request": json.dumps(envelope.payload, allow_nan=False)},
                files={"reference_audio": (
                    "reference.wav", decode_audio(envelope.reference_audio_b64), "audio/wav",
                )},
            )
        elif op == "warm":
            response = await self._client.post(
                f"/v1/models/{envelope.payload['model_id']}/warm", headers=headers,
            )
        elif op in {"analyze", "transliterate"}:
            response = await self._client.post(
                f"/v1/{op}", headers=headers, json=envelope.payload,
            )
        elif op == "models":
            response = await self._client.get("/v1/models", headers=headers)
        elif op == "setup_status":
            response = await self._client.get("/v1/setup", headers=headers)
        else:
            response = await self._client.post("/v1/setup", headers=headers)
            while response.status_code < 400:
                state = response.json()
                if not any(row["state"] in {
                    "discovering", "downloading", "verifying",
                } for row in state["models"].values()):
                    break
                await asyncio.sleep(self._poll_interval)
                response = await self._client.get("/v1/setup", headers=headers)
            if response.status_code < 400 and not response.json().get("ready"):
                return _error(envelope.operation_id, 503, "SETUP_FAILED")
        if response.status_code >= 400:
            code = "REQUEST_REJECTED"
            with contextlib.suppress(ValueError):
                problem = response.json()
                if isinstance(problem, dict) and problem.get("code") in WORKER_MESSAGES:
                    code = problem["code"]
            return _error(envelope.operation_id, response.status_code, code)
        try:
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
                if key.lower() in RESPONSE_HEADERS
            }
            if op in {"synthesize", "warm", "analyze", "transliterate"}:
                if response_headers.get("x-request-id") != str(envelope.operation_id):
                    raise ValueError("Mismatched worker identity")
            if op == "synthesize":
                if response_headers.get("x-model-id") != envelope.payload["model_id"]:
                    raise ValueError("Mismatched model")
                for field in (
                    "x-generation-time-sec", "x-audio-duration-sec", "x-load-time-sec",
                ):
                    value = float(response_headers[field])
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("Invalid metadata")
                    if field == "x-audio-duration-sec" and value == 0:
                        raise ValueError("Invalid metadata")
                audio = response.content
                if len(audio) > MAX_AUDIO_BYTES:
                    raise ValueError("Audio too large")
                with wave.open(io.BytesIO(audio), "rb") as wav:
                    if wav.getnframes() <= 0 or wav.getnchannels() not in {1, 2}:
                        raise ValueError("Invalid WAV")
                output = FlexOutput(
                    operation_id=envelope.operation_id, status_code=response.status_code,
                    headers=response_headers, audio_b64=base64.b64encode(audio).decode(),
                )
            else:
                body = response.json()
                if op in {"setup", "setup_status"}:
                    body["models"] = {
                        model_id: _clean_install(row) for model_id, row in body["models"].items()
                    }
                elif op == "models":
                    body["models"] = [{
                        "id": row["id"], "state": row["state"], "revision": row["revision"],
                        "install": _clean_install(row["install"]),
                    } for row in body["models"]]
                elif body.get("model_id") != _model_id(envelope):
                    raise ValueError("Mismatched helper model")
                output = FlexOutput(
                    operation_id=envelope.operation_id, status_code=response.status_code,
                    headers=response_headers, body=body,
                )
            return output.model_dump(mode="json", exclude_none=True)
        except (ValueError, KeyError, TypeError, wave.Error):
            return _error(envelope.operation_id, 502, "INVALID_RESULT")

