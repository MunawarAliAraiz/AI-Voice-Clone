"""Torch-free desktop inference using app-owned disposable cloud sessions."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from ..runpod.controller import CloudController
from .protocol import ModelStatus, SynthRequest, SynthResult


class ManagedRemoteScheduler:
    def __init__(self, cloud: CloudController) -> None:
        self.cloud = cloud

    async def status(self) -> tuple[ModelStatus, ...]:
        # Disk readiness survives compute release; reading the picker never rents a GPU.
        return await self.cloud.model_status()

    async def warm(self, model_id: str) -> None:
        async with self.cloud.session() as remote:
            await remote.warm(model_id)

    async def synthesize(self, request: SynthRequest) -> SynthResult:
        async with self.cloud.session() as remote:
            return await remote.synthesize(request)

    @contextlib.asynccontextmanager
    async def reserve_slot(self, reason: str) -> AsyncIterator[None]:
        yield

    async def shutdown(self) -> None:
        await self.cloud.shutdown()
