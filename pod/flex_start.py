"""Pinned Serverless SDK entrypoint; no public worker HTTP listener.

Prepared and tested offline only. No image/endpoint is qualified or enabled by
this module. The SDK owns the asyncio event loop and provider signals.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import UUID

from preflight import verify_imports
from start import prepare_cache_environment

SDK_VERSION = "1.12.0"
ProgressSender = Callable[[dict[str, str], dict[str, Any]], Awaitable[None]]


def prepare() -> Path:
    # Do not allow SDK debug/local-test/HTTP modes to expose input or write it.
    if len(sys.argv) != 1 or os.environ.get("RUNPOD_REALTIME_PORT", "0") not in {"", "0"}:
        raise ValueError("Only queued Serverless worker mode is supported")
    if not os.environ.get("RUNPOD_WEBHOOK_GET_JOB") or not os.environ.get(
        "RUNPOD_WEBHOOK_POST_OUTPUT",
    ):
        raise ValueError("Provider worker channels are required")
    os.environ["RUNPOD_LOG_LEVEL"] = "NOTSET"
    os.environ["RUNPOD_DEBUG_LEVEL"] = "NOTSET"
    # Our bridge generates its own opaque token. Avoid the legacy module-level
    # ASGI app initializer when a template happens to pass the old Pod token.
    os.environ.pop("POD_WORKER_TOKEN", None)
    namespace = prepare_cache_environment(mount_root="/runpod-volume")
    verify_imports(require_cuda=True)
    return namespace


def load_sdk():
    """Import only the pinned SDK, after disabling its payload logging."""
    if version("runpod") != SDK_VERSION:
        raise ValueError("The Serverless SDK version does not match the qualified lock")
    os.environ["RUNPOD_LOG_LEVEL"] = "NOTSET"
    os.environ["RUNPOD_DEBUG_LEVEL"] = "NOTSET"
    import runpod
    from runpod.serverless.modules.rp_logger import RunPodLogger

    # Also reset an already imported singleton; environment alone is insufficient.
    RunPodLogger().set_level("NOTSET")
    return runpod


async def send_progress(job: dict[str, str], progress: dict[str, Any]) -> None:
    """Await the exact async implementation behind the pinned SDK public hook.

    Public progress_update creates an untracked daemon HTTP thread. Its internal
    async helper lets cancellation await cleanup and keeps late progress bounded.
    This private hook is pinned to SDK1.12.0 and must be requalified on upgrade.
    """
    from runpod.http_client import AsyncClientSession
    from runpod.serverless.modules.rp_progress import _async_progress_update

    async with asyncio.timeout(5):
        async with AsyncClientSession() as session:
            await _async_progress_update(session, job, progress)


class WorkerRuntime:
    """Bridge lifetime is scoped to the SDK's actual job event loop."""

    def __init__(
        self,
        namespace: Path,
        *,
        bridge_factory=None,
        progress_sender: ProgressSender = send_progress,
    ) -> None:
        self.namespace = namespace
        self._factory = bridge_factory
        self._send_progress = progress_sender
        self._loop = None
        self._bridge = None
        self._lifecycle_task = None
        self._initialization_lock = None
        self._closed = False
        self._shutdown_event = None

    async def _ensure_started(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
            self._initialization_lock = asyncio.Lock()
        if self._loop is not loop or self._closed:
            raise RuntimeError("Worker event loop changed or closed")
        async with self._initialization_lock:
            if self._bridge is not None:
                return
            factory = self._factory
            if factory is None:
                from app.config import Settings
                from app.remote_worker.flex_handler import FlexHandler

                def factory(**kwargs):
                    return FlexHandler(settings=Settings(_env_file=None), **kwargs)

            bridge = factory(volume_namespace=self.namespace)
            await bridge.start()
            self._bridge = bridge
            self._shutdown_event = asyncio.Event()
            self._lifecycle_task = asyncio.create_task(self._lifetime())

    async def _lifetime(self) -> None:
        try:
            # SDK1.12.0 JobScaler uses asyncio.run(). When it exits after signal
            # drain (or local testing), asyncio cancels and awaits this supervisor.
            await self._shutdown_event.wait()
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._shutdown_event is not None:
            self._shutdown_event.set()
        if self._bridge is not None:
            await self._bridge.close()

    async def handle(self, job: dict[str, Any]) -> dict[str, Any]:
        expected = None
        try:
            if isinstance(job, dict) and isinstance(job.get("input"), dict):
                expected = UUID(str(job["input"].get("operation_id")))
        except (ValueError, TypeError, AttributeError):
            pass
        try:
            await self._ensure_started()
        except Exception:
            from app.runpod.flex_protocol import FlexOutput

            return FlexOutput(
                operation_id=expected or UUID(int=0),
                status_code=503,
                body={"code": "WORKER_UNAVAILABLE", "detail": "The cloud worker is unavailable."},
            ).model_dump(mode="json", exclude_none=True)

        async def callback(progress: dict[str, Any]) -> None:
            from app.inference.catalog import CATALOG
            from app.remote_worker.model_pins import AUXILIARY_PINS
            from app.runpod.flex_protocol import PROGRESS_STAGES

            # Telemetry contains known labels and this exact operation only.
            if (
                not isinstance(progress, dict)
                or set(progress) != {"protocol_version", "operation_id", "stage", "model_id"}
                or type(progress["protocol_version"]) is not int
                or progress["protocol_version"] != 1
                or expected is None
                or progress["operation_id"] != str(expected)
                or not isinstance(progress["stage"], str)
                or progress["stage"] not in PROGRESS_STAGES
                or not isinstance(progress["model_id"], str)
                or progress["model_id"]
                not in {spec.id for spec in CATALOG.specs} | set(AUXILIARY_PINS)
            ):
                return
            await self._send_progress({"id": job["id"]}, dict(progress))

        try:
            return await self._bridge.handle(job, callback)
        except Exception:
            from app.runpod.flex_protocol import FlexOutput

            return FlexOutput(
                operation_id=expected or UUID(int=0),
                status_code=500,
                body={"code": "INTERNAL_ERROR", "detail": "The cloud worker could not finish."},
            ).model_dump(mode="json", exclude_none=True)


def main(*, sdk=None, bridge_factory=None, progress_sender=send_progress) -> None:
    try:
        namespace = prepare()
        selected_sdk = sdk if sdk is not None else load_sdk()
        runtime = WorkerRuntime(
            namespace,
            bridge_factory=bridge_factory,
            progress_sender=progress_sender,
        )
        selected_sdk.serverless.start(
            {
                "handler": runtime.handle,
                "concurrency_modifier": lambda _current: 1,
                "refresh_worker": False,
            }
        )
    except Exception:
        # Provider/SDK exception text may contain signed channel URLs or user data.
        # Fixed categories only; do not print error, traceback or configuration.
        print(
            "Serverless worker could not start; check its qualified configuration.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
