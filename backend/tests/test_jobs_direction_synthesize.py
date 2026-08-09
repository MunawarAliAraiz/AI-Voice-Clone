"""
Multi-segment ("directed") generation, end-to-end through `JobRunner` — the
`synthesize` job handler branches on `params.segments`, synthesizing each
segment separately and joining with `concat_wavs_with_pauses`.

Against `FakeScheduler`, no torch, no GPU — but the scheduler double here
writes REAL silence WAVs (unlike the base `FakeScheduler`, which writes
`b"FAKE-NOT-AUDIO"` placeholder bytes, useless for asserting on joined
duration). This is the same substitution the fake `RuntimeKind.FAKE`
backend makes on the pod, just at the scheduler layer.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from app.config import Settings
from app.db.database import Database
from app.inference.catalog import CATALOG
from app.inference.protocol import SynthResult
from app.jobs import JobKind, JobRunner
from tests.fakes import FakeScheduler


class _WavWritingScheduler(FakeScheduler):
    """Writes real (silent) WAV files so joined output is real, readable
    audio — the concat step under test needs something to actually join."""

    async def synthesize(self, request):
        self.requests.append(request)
        if self.raise_on_synthesize is not None:
            raise self.raise_on_synthesize
        dur = float(request.params.get("_test_dur_sec", 0.3))
        n = int(dur * request.sample_rate)
        data = np.full((n,), 0.1, dtype=np.float32)
        Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(request.output_path), data, request.sample_rate, subtype="PCM_16")
        self.resident.add(request.model_id)
        return SynthResult(
            output_path=Path(request.output_path),
            duration_sec=dur,
            gen_time_sec=0.01,
            sample_rate=request.sample_rate,
            model_id=request.model_id,
        )


async def _setup(tmp_path: Path, sched: FakeScheduler | None = None):
    db = Database(tmp_path / "test.db")
    await db.connect()
    settings = Settings(data_dir=tmp_path)
    scheduler = sched or _WavWritingScheduler(catalog=CATALOG)
    profile = await db.create_profile(
        name="v", audio_path=tmp_path / "ref.wav", language="en", transcript=None,
        duration_sec=2.0, sample_rate=44100, peak_dbfs=-3.0, is_clipped=False,
    )
    runner = JobRunner(db, scheduler, CATALOG, settings)
    await runner.reap_stale()
    return runner, db, scheduler, profile["id"]


_ROUTE = {
    "model_id": "voxcpm2", "model_display_name": "VoxCPM 2", "transform": "none",
    "lossy": False, "rationale": "test", "source_script": "latin", "alternatives": [],
}


def _directed_params(tmp_path: Path, name: str) -> dict:
    return {
        "text": "Hello there. This is exciting!",
        "input_text": "Hello there. This is exciting!",
        "language": "en",
        "reference_audio": str(tmp_path / "ref.wav"),
        "reference_text": None,
        "output_path": str(tmp_path / f"{name}.wav"),
        "output_format": "wav",
        "sample_rate": 16000,
        "params": {},
        "speed": 1.0,
        "segments": [
            {
                "index": 0, "text": "Hello there.",
                "params": {"cfg_value": 1.6, "_test_dur_sec": 0.4},
                "speed": 1.0, "pause_after_ms": 180,
            },
            {
                "index": 1, "text": "This is exciting!",
                "params": {"cfg_value": 2.5, "_test_dur_sec": 0.3},
                "speed": 1.0, "pause_after_ms": 120,
            },
        ],
    }


async def test_directed_job_joins_segments_with_pauses(tmp_path: Path) -> None:
    runner, db, sched, pid = await _setup(tmp_path)
    await runner.start()

    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_directed_params(tmp_path, "d1"), route=_ROUTE,
        profile_id=pid,
    )
    await runner.wait_idle()

    finished = await db.get_job(job.id)
    assert finished["status"] == "succeeded", finished

    # Two segments, one scheduler call each — no single-shot fallback call.
    assert len(sched.requests) == 2
    assert sched.requests[0].text == "Hello there."
    assert sched.requests[0].params["cfg_value"] == 1.6
    assert sched.requests[1].text == "This is exciting!"
    assert sched.requests[1].params["cfg_value"] == 2.5

    # The joined file exists at the path recorded on the job (orphan rule) and
    # is real, readable audio whose duration is segments + the pause BETWEEN
    # them (0.4 + 0.18 + 0.3 = 0.88s) — the trailing pause_after_ms on the
    # LAST segment must not appear.
    import json

    result = json.loads(finished["result_json"])
    out_path = Path(tmp_path / "d1.wav")
    assert out_path.exists()
    data, sr = sf.read(str(out_path))
    assert abs(len(data) / sr - 0.88) < 0.02
    assert abs(result["duration_sec"] - 0.88) < 0.02
    assert result["segment_count"] == 2

    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_directed_job_cleans_up_temp_segment_files(tmp_path: Path) -> None:
    runner, db, _sched, pid = await _setup(tmp_path)
    await runner.start()

    await runner.enqueue(
        JobKind.SYNTHESIZE, params=_directed_params(tmp_path, "d2"), route=_ROUTE,
        profile_id=pid,
    )
    await runner.wait_idle()

    # Only the final joined file remains — no .seg0.wav / .seg1.wav litter.
    leftovers = list(tmp_path.glob("d2.seg*.wav"))
    assert leftovers == [], f"temp segment files not cleaned up: {leftovers}"
    assert (tmp_path / "d2.wav").exists()

    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_undirected_job_is_unaffected(tmp_path: Path) -> None:
    """A job with no `segments` key (the vast majority, and every job written
    by an older build) still takes the single-shot path — one scheduler call,
    no join. Regression guard for the branch added in this change."""
    runner, db, sched, pid = await _setup(tmp_path)
    await runner.start()

    params = _directed_params(tmp_path, "u1")
    params["segments"] = None
    params["text"] = "Hello there. This is exciting!"

    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=params, route=_ROUTE, profile_id=pid,
    )
    await runner.wait_idle()

    finished = await db.get_job(job.id)
    assert finished["status"] == "succeeded"
    assert len(sched.requests) == 1
    assert sched.requests[0].text == "Hello there. This is exciting!"

    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_directed_job_with_one_segment_still_joins_cleanly(tmp_path: Path) -> None:
    """A single-segment direction plan (short input) exercises the join path
    with nothing to actually join — must not error on a list of length 1."""
    runner, db, sched, pid = await _setup(tmp_path)
    await runner.start()

    params = _directed_params(tmp_path, "d3")
    params["segments"] = [params["segments"][0]]

    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=params, route=_ROUTE, profile_id=pid,
    )
    await runner.wait_idle()

    finished = await db.get_job(job.id)
    assert finished["status"] == "succeeded"
    assert len(sched.requests) == 1

    await runner.stop(drain_timeout_sec=1.0)
    await db.close()
