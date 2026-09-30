"""Desktop scheduler that reads an updated Pod pairing for each operation."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from ..exceptions import GenerationError
from ..runpod.worker_pair import WorkerPairStore
from .catalog import ModelCatalog
from .protocol import ModelStatus, SynthRequest, SynthResult
from .remote_scheduler import RemoteScheduler
from .spec import ModelState


class PairedRemoteScheduler:
    def __init__(
        self, data_dir: Path, catalog: ModelCatalog,
        *, store: WorkerPairStore | None = None,
    ) -> None:
        self._catalog = catalog
        self._store = store or WorkerPairStore(data_dir)

    def _remote(self) -> RemoteScheduler:
        try:
            pair = self._store.get()
        except (OSError, ValueError, KeyError) as exc:
            raise GenerationError("remote", "Cannot unlock the saved Pod connection") from exc
        if pair is None:
            raise GenerationError("remote", "Connect a Runpod Pod worker first")
        return RemoteScheduler(pair.url, pair.token, self._catalog)

    async def status(self) -> tuple[ModelStatus, ...]:
        # Keep the studio usable for local editing while its Pod is unpaired.
        try:
            pair = self._store.get()
        except (OSError, ValueError, KeyError) as exc:
            raise GenerationError("remote", "Cannot unlock the saved Pod connection") from exc
        if pair is None:
            return tuple(ModelStatus(spec=spec, state=ModelState.NOT_DOWNLOADED,
                                     est_wait_sec=0.0) for spec in self._catalog.specs)
        remote = RemoteScheduler(pair.url, pair.token, self._catalog)
        try:
            return await remote.status()
        finally:
            await remote.shutdown()

    async def warm(self, model_id: str) -> None:
        remote = self._remote()
        try:
            await remote.warm(model_id)
        finally:
            await remote.shutdown()

    async def synthesize(self, request: SynthRequest) -> SynthResult:
        remote = self._remote()
        try:
            return await remote.synthesize(request)
        finally:
            await remote.shutdown()

    @contextlib.asynccontextmanager
    async def reserve_slot(self, reason: str) -> AsyncIterator[None]:
        yield

    async def shutdown(self) -> None:
        pass
