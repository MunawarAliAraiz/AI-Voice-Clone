"""
Phase B on a real GPU, for the first time.

Run on the pod with the API venv (NO torch here — golden rule 2 holds even in a
smoke test; VRAM is read from `nvidia-smi`, not `torch.cuda`):

    cd /workspace/AI-Voice-Clone/backend
    VCS_VOXCPM_PYTHON=$PWD/.venv-voxcpm/bin/python \\
    VCS_GEMMA_TRANSLITERATOR_PYTHON=$PWD/.venv-gemma/bin/python \\
    .venv/bin/python scripts/phase_b_smoke.py

WHAT THIS IS ACTUALLY TESTING, AND WHY IT IS NOT `convert()` ALONE
-------------------------------------------------------------------
That Gemma produces Urdu is the easy half and a probe could show it. The claims
that have never been checked are about the SCHEDULER:

  1. `exclusive_gpu` really empties the card. An audio worker is warmed FIRST,
     on purpose — a conversion run on an already-idle GPU proves nothing about
     eviction, and eviction is the amendment to golden rule 3 that Phase B is
     built on.
  2. The Gemma worker is really KILLED. `TransliteratorScheduler` owns nothing
     between calls; if the VRAM does not come back, every subsequent generation
     OOMs and there is no idle timer to clean it up.
  3. The audio model comes back COLD afterwards and still synthesizes. That is
     the documented price of the design, and a price nobody has yet paid.

VRAM IS READ VIA NVML (`nvidia-smi`), NEVER `total - memory_allocated()`.
The latter sees only the calling process, and would report the card empty while
a worker subprocess holds 18 GB of it — which is the whole thing being measured
here.

THE CARD THIS RUNS ON IS PROBABLY NOT THE CARD THIS IS FOR. The design target
is **24 GB**. On anything larger, a peak that fits is an UPPER BOUND, not a
pass — so the peak is printed against 24576 MiB explicitly and the verdict says
so.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.inference.catalog import CATALOG
from app.inference.factory import make_worker_factory
from app.inference.scheduler import InferenceScheduler, SchedulerConfig
from app.inference.transliterator_scheduler import (
    GEMMA_TRANSLITERATOR_HF_REPO,
    GEMMA_TRANSLITERATOR_HF_REVISION,
    TransliteratorScheduler,
)

#: The card the budget was sized for, regardless of what is actually installed.
DESIGN_TARGET_MIB = 24576

WARM_MODEL = os.environ.get("VCS_SMOKE_WARM_MODEL", "voxcpm2")

#: One per conversion the transliterator knows. Sources are real text, not
#: lorem: the Devanagari line is the shape YouTube's Hindi ASR emits for Urdu
#: speech, which is the input this feature exists for.
CASES = [
    ("latin", "perso_arabic", "Kal office mein meeting hai aur mujhe report bhi taiyar karni hai."),
    ("devanagari", "roman", "मुझे समझ नहीं आ रहा कि ये कैसे हुआ, कल meeting भी है।"),
    ("devanagari", "perso_arabic", "मुझे समझ नहीं आ रहा कि ये कैसे हुआ, कल meeting भी है।"),
    ("arabic", "roman", "کل office میں meeting ہے اور مجھے report بھی تیار کرنی ہے۔"),
]


def _query(field: str) -> int:
    """
    One `nvidia-smi` field, as an int.

    Resolved from PATH deliberately — the driver installs it and its location
    varies by image, so an absolute path would be the fragile choice here.
    """
    out = subprocess.run(
        ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],  # noqa: S607
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip().splitlines()[0])


def used_mib() -> int:
    """Whole-card usage, across every process. See the module docstring."""
    return _query("memory.used")


def total_mib() -> int:
    return _query("memory.total")


class Sampler:
    """Polls whole-card VRAM in the background so a PEAK is observed, not a
    pair of endpoints that would both miss the load."""

    def __init__(self) -> None:
        self.peak = 0
        self._stop = False

    async def run(self) -> None:
        while not self._stop:
            self.peak = max(self.peak, used_mib())
            await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._stop = True


async def main() -> int:
    settings = Settings()
    if not settings.gemma_transliterator_python:
        print("VCS_GEMMA_TRANSLITERATOR_PYTHON is not set", file=sys.stderr)
        return 2

    card = total_mib()
    print(f"card: {card} MiB total | design target: {DESIGN_TARGET_MIB} MiB")
    print(f"repo: {GEMMA_TRANSLITERATOR_HF_REPO}@{GEMMA_TRANSLITERATOR_HF_REVISION[:12]}")
    baseline = used_mib()
    print(f"[0] baseline (nothing loaded):        {baseline} MiB")

    factory = make_worker_factory(
        interpreters=settings.interpreters(), env=dict(os.environ), cwd=settings.worker_cwd
    )
    scheduler = InferenceScheduler(
        CATALOG, factory,
        SchedulerConfig(budget_mb=settings.budget_mb, max_workers=settings.max_workers),
    )
    transliterator = TransliteratorScheduler(
        python_executable=settings.gemma_transliterator_python,
        inference_scheduler=scheduler,
        env=dict(os.environ),
        cwd=settings.worker_cwd,
    )

    failures: list[str] = []
    try:
        # (1) An audio worker FIRST. Converting on an idle card would prove
        # nothing about the eviction this whole design rests on.
        t0 = time.time()
        await scheduler.warm(WARM_MODEL)
        warm_mib = used_mib()
        print(f"[1] after warming {WARM_MODEL}: {warm_mib} MiB "
              f"(+{warm_mib - baseline}, {time.time() - t0:.0f}s)")
        if warm_mib - baseline < 1000:
            failures.append("warming the audio model did not visibly allocate VRAM")

        peaks: list[int] = []
        for source, target, text in CASES:
            print(f"\n--- {source} -> {target} ---")
            print(f"    in : {text}")
            sampler = Sampler()
            task = asyncio.create_task(sampler.run())
            t0 = time.time()
            try:
                result = await transliterator.convert(
                    text=text, source_script=source, target_script=target
                )
            finally:
                sampler.stop()
                await task
            elapsed = time.time() - t0
            after = used_mib()
            peaks.append(sampler.peak)
            print(f"    out: {result.text}")
            print(f"    load {result.load_time_sec:.1f}s | gen {result.gen_time_sec:.1f}s "
                  f"| wall {elapsed:.0f}s")
            print(f"    peak {sampler.peak} MiB | after {after} MiB")

            if not result.text.strip():
                failures.append(f"{source}->{target}: empty output")
            # (2) The worker must be GONE. Back near where the audio model left
            # it, not merely lower than the peak.
            if after > warm_mib + 2000:
                failures.append(
                    f"{source}->{target}: {after} MiB still held after the "
                    f"conversion (audio-warm level was {warm_mib})"
                )

        # (3) Audio still works, from cold. The documented price of the design.
        print("\n--- audio after Gemma ---")
        t0 = time.time()
        await scheduler.warm(WARM_MODEL)
        reload_mib = used_mib()
        print(f"    re-warmed {WARM_MODEL} in {time.time() - t0:.0f}s -> {reload_mib} MiB")
        if reload_mib - baseline < 1000:
            failures.append("the audio model did not reload after the conversions")

        worst = max(peaks) if peaks else 0
        print(f"\npeak VRAM across all conversions: {worst} MiB")
        if worst > DESIGN_TARGET_MIB:
            failures.append(
                f"peak {worst} MiB exceeds the {DESIGN_TARGET_MIB} MiB design target — "
                f"this ran on a {card} MiB card and would NOT fit the intended one"
            )
        elif card > DESIGN_TARGET_MIB:
            print(f"  fits the {DESIGN_TARGET_MIB} MiB target, but measured on a "
                  f"{card} MiB card — an upper bound, not a demonstration")
    finally:
        await transliterator.shutdown()
        await scheduler.shutdown()

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASSED — but the OUTPUT TEXT above is not verified by this script.")
    print("Only latin->perso_arabic has ever passed a listening gate; read the")
    print("other three yourself before anything presents them as working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
