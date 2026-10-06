"""A labelled local Windows demo reference, only when a user starts a model test."""
from __future__ import annotations

import asyncio
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from .db import Database
from .exceptions import GenerationError, ProfileNotFoundError

DEMO_TEXT = (
    "Hello. This is a computer generated sample voice for a short speech test. "
    "We are checking that the model can create audio."
)
DEMO_NAME = "Demo voice - computer generated"
_lock = asyncio.Lock()


def _create(path: Path) -> tuple[float, int, float]:
    if sys.platform != "win32":
        raise GenerationError(
            "test", "Add a reference voice before testing speech models on this computer."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part.wav")
    env = os.environ.copy()
    env["VCS_TEST_DEMO_PATH"] = str(temporary.resolve())
    env["VCS_TEST_DEMO_TEXT"] = DEMO_TEXT
    script = """Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voice.SetOutputToWaveFile($env:VCS_TEST_DEMO_PATH)
  $voice.Speak($env:VCS_TEST_DEMO_TEXT)
} finally { $voice.Dispose() }
"""
    try:
        executable = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / (
            "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        subprocess.run(  # noqa: S603 -- fixed local script; data passed through environment
                       [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, check=True, timeout=45, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        audio, rate = sf.read(temporary, always_2d=True)
        if not audio.size or not np.isfinite(audio).all():
            raise ValueError("Invalid sample")
        peak = float(np.max(np.abs(audio)))
        if peak < 1e-5:
            raise ValueError("Silent sample")
        duration = len(audio) / rate
        temporary.replace(path)
        return duration, rate, 20 * math.log10(peak)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise GenerationError(
            "test", "The sample voice could not be created. Add a reference voice and try again."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


async def reference_profile(db: Database, data_dir: Path, profile_id: int | None):
    if profile_id is not None:
        profile = await db.get_profile(profile_id)
        if profile is None or not profile["is_active"]:
            raise ProfileNotFoundError(profile_id)
        if not await asyncio.to_thread(Path(profile["audio_path"]).is_file):
            raise GenerationError(
                "test", "This voice's reference file is missing. Choose another voice."
            )
        return profile
    async with _lock:
        profiles = await db.list_profiles()
        valid = await asyncio.to_thread(
            lambda: next((p for p in profiles if Path(p["audio_path"]).is_file()), None)
        )
        if valid is not None:
            return valid
        path = data_dir / "references" / "model-test-demo.wav"
        duration, rate, peak = await asyncio.to_thread(_create, path)
        return await db.create_profile(name=DEMO_NAME, audio_path=path, language="en",
                                       transcript=DEMO_TEXT, duration_sec=duration,
                                       sample_rate=rate, peak_dbfs=peak, is_clipped=peak >= 0)
