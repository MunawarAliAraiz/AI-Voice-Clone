"""
Fake worker — real implementation, not a stub.

This is test infrastructure, so it is written now rather than in Wave 2: B1's
scheduler tests are the highest-value tests in the suite, and every one of them
runs against this instead of a GPU. That is what makes
`pytest -m "not gpu"` finish in ~30 seconds on a machine with no CUDA.

It implements `WorkerHandle` faithfully, including the parts that are easy to
get wrong: load latency (so double-load races are observable), configurable
crashes, and an `in_flight` counter that makes unload-during-inference DETECTABLE
rather than merely unlikely.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.inference.protocol import WireOp, WireResponse

__all__ = ["FakeWorker", "FakeWorkerCrash"]


class FakeWorkerCrash(Exception):
    """Raised by a FakeWorker configured to die on a given operation."""


class FakeWorker:
    """
    An in-process stand-in for a worker subprocess.

    Assertions the scheduler tests rely on:

        load_calls          how many times a checkpoint was loaded. A double
                            load under concurrency means the slot leaked.
        max_concurrent      peak simultaneous synths. MUST stay 1 — anything
                            higher means two requests held the GPU at once.
        unload_during_synth set True if unload/kill happened while a synth was
                            in flight. MUST stay False. This is the
                            use-after-free the whole design exists to prevent.
    """

    def __init__(
        self,
        runtime: str,
        *,
        load_delay_sec: float = 0.01,
        synth_delay_sec: float = 0.01,
        crash_on: WireOp | None = None,
        hang_on: WireOp | None = None,
    ) -> None:
        self._runtime = runtime
        self._loaded_model_id: str | None = None
        self._alive = True
        self._pid = 424242

        self.load_delay_sec = load_delay_sec
        self.synth_delay_sec = synth_delay_sec
        self.crash_on = crash_on
        #: Never returns, so timeout-then-SIGKILL can be exercised.
        self.hang_on = hang_on

        # Observability
        self.load_calls = 0
        self.synth_calls = 0
        self.unload_calls = 0
        self.kill_calls = 0
        self.calls: list[tuple[WireOp, dict[str, Any]]] = []

        self.in_flight = 0
        self.max_concurrent = 0
        self.unload_during_synth = False

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
        return self._pid if self._alive else None

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def start(self) -> None:
        self._alive = True

    async def call(
        self, op: WireOp, payload: dict[str, Any], *, timeout: float
    ) -> WireResponse:
        self.calls.append((op, payload))
        self._next_id += 1
        req_id = self._next_id

        if not self._alive:
            return WireResponse(
                id=req_id, ok=False, error_code="WORKER_CRASHED",
                error_message="worker is not alive",
            )

        if self.hang_on is op:
            await asyncio.sleep(timeout * 10)  # scheduler must time out and kill

        if self.crash_on is op:
            self._alive = False
            return WireResponse(
                id=req_id, ok=False, error_code="WORKER_CRASHED",
                error_message=f"simulated crash on {op.value}",
            )

        if op is WireOp.LOAD:
            return await self._load(req_id, payload)
        if op is WireOp.SYNTH:
            return await self._synth(req_id, payload)
        if op is WireOp.UNLOAD:
            return self._unload(req_id)
        if op is WireOp.PING:
            return WireResponse(
                id=req_id, ok=True,
                result={"loaded_model_id": self._loaded_model_id, "alive": True},
            )
        if op is WireOp.SHUTDOWN:
            self._alive = False
            return WireResponse(id=req_id, ok=True)

        return WireResponse(
            id=req_id, ok=False, error_code="UNKNOWN_OP", error_message=op.value
        )

    async def kill(self, *, grace_sec: float = 5.0) -> None:
        self.kill_calls += 1
        if self.in_flight > 0:
            # If this trips, the scheduler evicted while a synth was running.
            self.unload_during_synth = True
        self._alive = False
        self._loaded_model_id = None

    # ── Internals ────────────────────────────────────────────────────────────

    async def _load(self, req_id: int, payload: dict[str, Any]) -> WireResponse:
        self.load_calls += 1
        if self.in_flight > 0:
            self.unload_during_synth = True
        # A real load takes seconds; the delay is what makes a double-load race
        # observable at all. With an instant load, the bug hides.
        await asyncio.sleep(self.load_delay_sec)
        self._loaded_model_id = payload.get("model_id")
        return WireResponse(
            id=req_id, ok=True,
            result={"model_id": self._loaded_model_id, "load_time_sec": self.load_delay_sec},
        )

    async def _synth(self, req_id: int, payload: dict[str, Any]) -> WireResponse:
        self.synth_calls += 1
        self.in_flight += 1
        self.max_concurrent = max(self.max_concurrent, self.in_flight)
        try:
            await asyncio.sleep(self.synth_delay_sec)
            out = Path(payload.get("output_path", "/tmp/fake.wav"))
            return WireResponse(
                id=req_id,
                ok=True,
                result={
                    "output_path": str(out),
                    "duration_sec": 1.0,
                    "gen_time_sec": self.synth_delay_sec,
                    "sample_rate": payload.get("sample_rate", 44100),
                    "model_id": self._loaded_model_id,
                },
            )
        finally:
            self.in_flight -= 1

    def _unload(self, req_id: int) -> WireResponse:
        self.unload_calls += 1
        if self.in_flight > 0:
            self.unload_during_synth = True
        self._loaded_model_id = None
        return WireResponse(id=req_id, ok=True)
