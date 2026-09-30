"""A desktop Pod pair takes effect without restarting the local job queue."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.exceptions import GenerationError
from app.inference.catalog import CATALOG
from app.inference.paired_remote_scheduler import PairedRemoteScheduler
from app.inference.protocol import ModelStatus, SynthRequest, SynthResult
from app.inference.spec import ModelState
from app.runpod.worker_pair import WorkerPair


class FakeStore:
    pair: WorkerPair | None = None

    def get(self) -> WorkerPair | None:
        return self.pair


@pytest.mark.asyncio
async def test_pair_switches_scheduler_without_restart(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    class FakeRemote:
        def __init__(self, url: str, token: str, catalog) -> None:
            seen.append(url)
            assert token == "a" * 32
            assert catalog is CATALOG

        async def status(self) -> tuple[ModelStatus, ...]:
            spec = CATALOG.get("voxcpm2")
            assert spec is not None
            return (ModelStatus(spec=spec, state=ModelState.RESIDENT, est_wait_sec=0),)

        async def synthesize(self, request: SynthRequest) -> SynthResult:
            request.output_path.write_bytes(b"RIFF")
            return SynthResult(output_path=request.output_path, duration_sec=1,
                               gen_time_sec=1, sample_rate=24000,
                               model_id=request.model_id, load_time_sec=0)

        async def shutdown(self) -> None:
            pass

    monkeypatch.setattr("app.inference.paired_remote_scheduler.RemoteScheduler", FakeRemote)
    store = FakeStore()
    scheduler = PairedRemoteScheduler(tmp_path, CATALOG, store=store)
    assert all(s.state is ModelState.NOT_DOWNLOADED for s in await scheduler.status())
    with pytest.raises(GenerationError, match="Connect a Runpod Pod worker"):
        await scheduler.synthesize(SynthRequest(model_id="voxcpm2", text="Hello",
            reference_audio=tmp_path / "reference.wav", output_path=tmp_path / "out.wav"))

    store.pair = WorkerPair("abc123xyz", "a" * 32)
    assert (await scheduler.status())[0].state is ModelState.RESIDENT
    request = SynthRequest(model_id="voxcpm2", text="Hello",
        reference_audio=tmp_path / "reference.wav", output_path=tmp_path / "out.wav")
    assert (await scheduler.synthesize(request)).output_path.read_bytes() == b"RIFF"
    assert seen == ["https://abc123xyz-8000.proxy.runpod.net"] * 2


def test_worker_pair_rejects_bad_id_and_token() -> None:
    with pytest.raises(ValueError, match="Pod ID"):
        WorkerPair("abc123.example.com", "a" * 32)
    with pytest.raises(ValueError, match="URL-safe"):
        WorkerPair("abc123xyz", "a" * 24 + "\n")
