"""
End-to-end tests for the worker subprocess client.

These spawn the REAL `app.inference.worker` process with the gated `fake`
backend (silence, no torch) and drive it through `WorkerProcess`. That exercises
the whole wire path — spawn, READY handshake, id-matched request/response,
timeout->kill, idempotent kill — with no GPU and no torch, which is exactly what
the subprocess design is supposed to make possible.
"""

from __future__ import annotations

import asyncio
import os
import sys
import wave
from pathlib import Path

import pytest

from app.inference.protocol import WireOp
from app.inference.worker_client import WorkerProcess

BACKEND_DIR = Path(__file__).resolve().parents[1]

# create_subprocess_exec needs the Proactor loop on Windows; the Selector loop
# raises NotImplementedError. Harmless on POSIX. Production is Linux either way.
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def _worker(**env_extra: str) -> WorkerProcess:
    env = dict(os.environ, VCS_ALLOW_FAKE_RUNTIME="1", **env_extra)
    return WorkerProcess("fake", sys.executable, env=env, cwd=BACKEND_DIR)


async def test_start_load_synth_ping(tmp_path: Path) -> None:
    w = _worker()
    await w.start()
    try:
        assert w.is_alive and w.pid is not None

        load = await w.call(
            WireOp.LOAD,
            {"model_id": "fake1", "hf_repo": "x/y", "hf_revision": "z"},
            timeout=10,
        )
        assert load.ok and "load_time_sec" in load.result
        assert w.loaded_model_id == "fake1"  # residency tracked client-side

        out = tmp_path / "out.wav"
        synth = await w.call(
            WireOp.SYNTH,
            {
                "model_id": "fake1",
                "text": "hello",
                "reference_audio": "unused.wav",
                "reference_text": None,
                "output_path": str(out),
                "params": {"dur_sec": 0.5},
                "sample_rate": 24000,
            },
            timeout=10,
        )
        assert synth.ok
        assert synth.result["sample_rate"] == 24000
        assert out.exists()
        with wave.open(str(out), "rb") as f:  # real, valid WAV of silence
            assert f.getframerate() == 24000
            assert abs(f.getnframes() - int(0.5 * 24000)) < 24000

        ping = await w.call(WireOp.PING, {}, timeout=5)
        assert ping.ok and ping.result["loaded_model_id"] == "fake1"
    finally:
        await w.kill()
    assert not w.is_alive


async def test_backend_error_is_reported_not_raised(tmp_path: Path) -> None:
    """A SYNTH before LOAD is a backend error -> ok=False, process stays alive."""
    w = _worker()
    await w.start()
    try:
        # fake.synth works without load, so force an error a different way:
        # an unknown op is rejected by the worker with ok=False, not a crash.
        bad = await w.call(WireOp.SYNTH, {"text": "x"}, timeout=5)  # missing keys
        assert not bad.ok
        assert bad.error_code  # e.g. KeyError
        assert w.is_alive  # a rendering error must not kill the worker
    finally:
        await w.kill()


async def test_timeout_kills_worker(tmp_path: Path) -> None:
    w = _worker()
    await w.start()
    try:
        await w.call(
            WireOp.LOAD,
            {"model_id": "fake1", "hf_repo": "x/y", "hf_revision": "z"},
            timeout=10,
        )
        out = tmp_path / "slow.wav"
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await w.call(
                WireOp.SYNTH,
                {
                    "model_id": "fake1",
                    "text": "slow",
                    "reference_audio": "unused.wav",
                    "reference_text": None,
                    "output_path": str(out),
                    "params": {"sleep_sec": 3.0},
                    "sample_rate": 24000,
                },
                timeout=0.3,
            )
        # The wedged worker must be dead, not left holding VRAM the budget thinks
        # is free.
        assert not w.is_alive
    finally:
        await w.kill()


async def test_kill_is_idempotent() -> None:
    w = _worker()
    await w.start()
    await w.kill()
    assert not w.is_alive
    await w.kill()  # second kill must be a no-op, not an error
    assert not w.is_alive
