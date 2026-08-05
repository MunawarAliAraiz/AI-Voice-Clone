"""
AI Voice Clone Studio — Worker subprocess client.

Runs in the API process, so NO TORCH HERE. This is the API-side half of the
line-delimited JSON protocol in `protocol.py`; the worker-side half is
`inference/worker.py`, spawned as a subprocess and never imported.

Every method is called while the scheduler holds the GPU slot, so this class
needs no locking of its own — except that `_next_id` must stay monotonic. A
response whose id does not match the request means the stdio stream has
desynchronized (a partial line from a crashed generation, say). On desync we
KILL rather than resynchronize: a worker whose responses are off by one returns
the wrong audio for a request, which is the exact class of bug this rewrite
exists to eliminate.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from pathlib import Path
from typing import Any

from .protocol import WireOp, WireResponse

__all__ = ["WorkerProcess"]

logger = logging.getLogger("app.inference.worker")

#: The worker is launched as `python -m app.inference.worker <runtime>`.
_WORKER_MODULE = "app.inference.worker"


class WorkerProcess:
    """A live worker subprocess. Implements `WorkerHandle`."""

    def __init__(
        self,
        runtime: str,
        python_executable: str,
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._runtime = runtime
        self._python = python_executable
        self._env = env or {}
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._loaded_model_id: str | None = None
        self._next_id = 0
        self._stderr_task: asyncio.Task[None] | None = None

    # ── WorkerHandle ─────────────────────────────────────────────────────────

    @property
    def runtime(self) -> str:
        return self._runtime

    @property
    def loaded_model_id(self) -> str | None:
        return self._loaded_model_id

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """
        Spawn the subprocess and wait for its READY handshake.

        stderr is drained into the app log by a background task for the whole
        lifetime of the process. If it were left unread, a chatty runtime — and
        every torch stack is chatty — fills the OS pipe buffer and the worker
        blocks forever on a write, presenting as a hang with no error anywhere.
        """
        self._proc = await asyncio.create_subprocess_exec(
            self._python,
            "-m",
            _WORKER_MODULE,
            self._runtime,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env or None,
            cwd=str(self._cwd) if self._cwd else None,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        # First stdout line is the READY handshake. A dead process or a
        # ready:false payload is a fatal startup failure.
        try:
            line = await self._readline()
        except EOFError as exc:
            await self.kill()
            raise RuntimeError(
                f"{self._runtime} worker exited before READY"
            ) from exc
        handshake = json.loads(line)
        if not handshake.get("ready"):
            await self.kill()
            raise RuntimeError(
                f"{self._runtime} worker failed to start: "
                f"{handshake.get('error', 'no reason given')}"
            )

    async def call(
        self, op: WireOp, payload: dict[str, Any], *, timeout: float
    ) -> WireResponse:
        """
        Send one request, await its response.

        On timeout: kill the process and raise. Do NOT return and leave it
        running — a wedged CUDA kernel holds VRAM the budget believes is free.
        On id mismatch or EOF: kill and raise; the stream is not trustworthy.
        """
        if not self.is_alive or self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"{self._runtime} worker is not alive")

        rid = self._next_id
        self._next_id += 1
        frame = json.dumps({"id": rid, "op": str(op), "payload": payload}) + "\n"

        self._proc.stdin.write(frame.encode("utf-8"))
        try:
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            await self.kill()
            raise RuntimeError(f"{self._runtime} worker died on write") from exc

        try:
            line = await asyncio.wait_for(self._readline(), timeout=timeout)
        except TimeoutError:
            await self.kill()
            raise  # scheduler maps TimeoutError -> GenerationTimeoutError
        except EOFError as exc:
            await self.kill()
            raise RuntimeError(f"{self._runtime} worker died mid-request") from exc

        data = json.loads(line)
        if int(data.get("id", -1)) != rid:
            # Off-by-one desync: the stream cannot be trusted. Kill, never resync.
            await self.kill()
            raise RuntimeError(
                f"{self._runtime} worker stream desynchronized "
                f"(want id={rid}, got {data.get('id')})"
            )

        response = WireResponse(
            id=rid,
            ok=bool(data.get("ok")),
            result=data.get("result") or {},
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            traceback=data.get("traceback"),
        )
        # Track residency so PING/status need not touch the worker.
        if response.ok:
            if op == WireOp.LOAD:
                self._loaded_model_id = payload.get("model_id")
            elif op == WireOp.UNLOAD:
                self._loaded_model_id = None
        return response

    async def kill(self, *, grace_sec: float = 5.0) -> None:
        """SHUTDOWN, then SIGKILL after `grace_sec`. Idempotent; safe if dead."""
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            # Best-effort graceful shutdown; a wedged worker will ignore it and
            # be killed below, so keep the window short.
            with contextlib.suppress(Exception):
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.write(
                        (json.dumps({"id": -1, "op": str(WireOp.SHUTDOWN)}) + "\n").encode()
                    )
                    await asyncio.wait_for(proc.stdin.drain(), timeout=1.0)
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace_sec)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.send_signal(signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await proc.wait()
        # Stop draining stderr once the process is gone.
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stderr_task
            self._stderr_task = None
        self._loaded_model_id = None

    # ── internals ────────────────────────────────────────────────────────────

    async def _readline(self) -> str:
        """Read one protocol line from stdout, or raise EOFError if the pipe closed."""
        assert self._proc is not None and self._proc.stdout is not None
        raw = await self._proc.stdout.readline()
        if not raw:  # EOF — process exited
            raise EOFError
        return raw.decode("utf-8").strip()

    async def _drain_stderr(self) -> None:
        """Forward the worker's stderr to the app log, line by line, forever."""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                raw = await self._proc.stderr.readline()
                if not raw:
                    return
                logger.debug(
                    "[%s] %s", self._runtime, raw.decode("utf-8", "replace").rstrip()
                )
        except asyncio.CancelledError:
            return
