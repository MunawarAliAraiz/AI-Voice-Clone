"""
AI Voice Clone Studio — Fake runtime backend.

Deterministic SILENCE, never audio that could be mistaken for a clone. Exists so
the worker protocol and the scheduler can be exercised end-to-end on a machine
with no GPU and no torch. Gated behind VCS_ALLOW_FAKE_RUNTIME (see make_backend)
so it can never be reached by accident in production — the predecessor shipping a
440Hz sine wave with HTTP 200 is the exact failure this gate exists to prevent.

Uses only the stdlib `wave` module, so importing it pulls in nothing heavy.
"""

from __future__ import annotations

import time
import wave
from typing import Any

__all__ = ["FakeBackend"]


class FakeBackend:
    """Writes silence. `params` understands `dur_sec` and `sleep_sec` (for tests)."""

    runtime = "fake"

    def __init__(self) -> None:
        self.loaded_model_id: str | None = None

    def load(self, model_id: str, hf_repo: str, hf_revision: str) -> float:
        self.loaded_model_id = model_id
        return 0.0

    def synth(
        self,
        *,
        text: str,
        reference_audio: str,
        output_path: str,
        params: dict[str, Any],
        sample_rate: int,
        reference_text: str | None = None,
    ) -> dict[str, Any]:
        sleep_sec = float(params.get("sleep_sec", 0.0))
        if sleep_sec:  # lets a test drive the scheduler's generation timeout
            time.sleep(sleep_sec)
        dur = float(params.get("dur_sec", 0.5))
        frames = int(dur * sample_rate)
        t0 = time.time()
        with wave.open(output_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(b"\x00\x00" * frames)
        return {
            "duration_sec": dur,
            "gen_time_sec": time.time() - t0,
            "sample_rate": sample_rate,
        }

    def unload(self) -> None:
        self.loaded_model_id = None
