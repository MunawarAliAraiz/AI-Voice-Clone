"""
Fake scheduler — real implementation, not a stub.

Substituted for `InferenceScheduler` in every API test. Because B2 depends on
`SchedulerProtocol` rather than the concrete class, the entire HTTP layer can be
tested with no torch, no CUDA, and no subprocess.

It does NOT emulate the GPU-slot invariant — that is the real scheduler's job
and is tested against `FakeWorker` instead. This class exists to let API tests
assert on status codes, problem+json bodies, media tokens, and route chips.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.inference.catalog import CATALOG, ModelCatalog
from app.inference.protocol import ModelStatus, SynthRequest, SynthResult
from app.inference.spec import ModelState

__all__ = ["FakeScheduler"]


class FakeScheduler:
    """
    Canned `SchedulerProtocol` implementation.

    Configure `raise_on_synthesize` to make the next call fail, and assert on
    `requests` to check that routing handed down what you expected — in
    particular that `request.text` is the POST-TRANSFORM string, not the user's
    original input.
    """

    def __init__(
        self,
        catalog: ModelCatalog | None = None,
        *,
        synth_delay_sec: float = 0.0,
        duration_sec: float = 1.5,
        raise_on_synthesize: Exception | None = None,
    ) -> None:
        self._catalog = catalog or CATALOG
        self.synth_delay_sec = synth_delay_sec
        self.duration_sec = duration_sec
        self.raise_on_synthesize = raise_on_synthesize

        #: Every SynthRequest received, in order.
        self.requests: list[SynthRequest] = []
        self.warmed: list[str] = []
        self.shutdown_calls = 0
        #: Model ids reported RESIDENT by `status()`.
        self.resident: set[str] = set()

    async def synthesize(self, request: SynthRequest) -> SynthResult:
        self.requests.append(request)
        if self.raise_on_synthesize is not None:
            raise self.raise_on_synthesize
        if self.synth_delay_sec:
            await asyncio.sleep(self.synth_delay_sec)

        # Write a small placeholder so FileResponse and Range tests have a real
        # file to serve. Deliberately NOT audio: nothing this class produces may
        # ever be mistakable for a clone.
        out = Path(request.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKE-NOT-AUDIO")

        self.resident.add(request.model_id)
        return SynthResult(
            output_path=out,
            duration_sec=self.duration_sec,
            gen_time_sec=0.01,
            sample_rate=request.sample_rate,
            model_id=request.model_id,
        )

    async def status(self) -> tuple[ModelStatus, ...]:
        return tuple(
            ModelStatus(
                spec=spec,
                state=ModelState.RESIDENT if spec.id in self.resident else ModelState.COLD,
                est_wait_sec=0.0 if spec.id in self.resident else spec.est_load_sec,
            )
            for spec in self._catalog.specs
        )

    async def warm(self, model_id: str) -> None:
        self._catalog.require(model_id)
        self.warmed.append(model_id)
        self.resident.add(model_id)

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.resident.clear()
