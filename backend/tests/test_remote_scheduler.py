"""Remote synthesis saves only matching-model audio after a successful reply."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.exceptions import GenerationError
from app.inference.catalog import CATALOG
from app.inference.protocol import SynthRequest
from app.inference.remote_scheduler import RemoteScheduler


@pytest.mark.asyncio
async def test_synthesize_roundtrip(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.url.path == "/v1/synthesize"
        assert b"voxcpm2" in request.content
        return httpx.Response(200, content=b"RIFF-audio", headers={
            "X-Model-Id": "voxcpm2", "X-Audio-Duration-Sec": "1.5",
            "X-Generation-Time-Sec": "2.25", "X-Load-Time-Sec": "0.1",
        })

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"sample")
    scheduler = RemoteScheduler("https://pod.example", "secret", CATALOG,
                                transport=httpx.MockTransport(handler))
    try:
        result = await scheduler.synthesize(SynthRequest(
            model_id="voxcpm2", text="Hello", reference_audio=reference,
            output_path=tmp_path / "generated.wav",
        ))
        assert result.duration_sec == 1.5
        assert result.output_path.read_bytes() == b"RIFF-audio"
    finally:
        await scheduler.shutdown()


@pytest.mark.asyncio
async def test_model_swap_is_rejected(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"sample")
    scheduler = RemoteScheduler("https://pod.example", "secret", CATALOG,
        transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=b"wrong", headers={"X-Model-Id": "omnivoice_urdu"})))
    try:
        with pytest.raises(GenerationError, match="changed the selected model"):
            await scheduler.synthesize(SynthRequest(
                model_id="voxcpm2", text="Hello", reference_audio=reference,
                output_path=tmp_path / "generated.wav",
            ))
        assert not (tmp_path / "generated.wav").exists()
    finally:
        await scheduler.shutdown()


@pytest.mark.asyncio
async def test_status_requires_matching_protocol() -> None:
    scheduler = RemoteScheduler("https://pod.example", "secret", CATALOG,
        transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"protocol_version": 2, "models": []})))
    try:
        with pytest.raises(GenerationError, match="protocol version"):
            await scheduler.status()
    finally:
        await scheduler.shutdown()
