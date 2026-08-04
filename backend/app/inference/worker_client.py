"""
AI Voice Clone Studio — Worker subprocess client.

STUB. Wave 0 fixes the signatures. B1 implements in Wave 2.

Runs in the API process, so NO TORCH HERE. This is the API-side half of the
line-delimited JSON protocol in `protocol.py`; the worker-side half lives in
`inference/worker.py`, which is spawned as a subprocess and never imported.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .protocol import WireOp, WireResponse

__all__ = ["WorkerProcess"]


class WorkerProcess:
    """
    A live worker subprocess. Implements `WorkerHandle`.

    Every method is called while the scheduler holds the GPU slot, so this class
    needs no locking of its own — with one exception: `_next_id` must stay
    monotonic, since a response whose id does not match the request means the
    stdio stream has desynchronized.

    On desync, KILL. Do not attempt to resynchronize: the stream may hold a
    partial line from a crashed generation, and a "recovered" worker whose
    responses are off by one silently returns the wrong audio for a request —
    which is the exact class of bug this rewrite exists to eliminate.
    """

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

        stderr is drained into the app log continuously by a background task. If
        it is left unread, a chatty runtime — and every torch stack is chatty —
        fills the OS pipe buffer and the worker blocks forever on a write, which
        presents as a hang with no error anywhere.
        """
        raise NotImplementedError("Wave 2 / B1")

    async def call(
        self, op: WireOp, payload: dict[str, Any], *, timeout: float
    ) -> WireResponse:
        """
        Send one request, await its response.

        On timeout: kill the process and raise. Do NOT return and leave it
        running — a wedged CUDA kernel holds VRAM the budget believes is free.
        """
        raise NotImplementedError("Wave 2 / B1")

    async def kill(self, *, grace_sec: float = 5.0) -> None:
        """SHUTDOWN, then SIGKILL after `grace_sec`. Idempotent; safe if dead."""
        raise NotImplementedError("Wave 2 / B1")
