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
that matter are about the SCHEDULER, and **every one of them inverted on
2026-08-17** when the first run of this script killed the load-convert-unload
design. It used to assert the card was emptied and the VRAM returned; it now
asserts the opposite, because that is what the measurements bought:

  1. Gemma and the audio models CO-RESIDE. An audio worker is warmed first, on
     purpose — a conversion on an idle GPU would prove nothing either way.
  2. The audio worker SURVIVES the conversion. Under the old design it was
     evicted and the next generation paid a 176 s reload; `reserve_slot` holds
     the slot and evicts nothing.
  3. The SECOND conversion onward costs only generation. That is the whole
     10-20 s target: 2.7-5.1 s of work against a 150-330 s load that now
     happens once.

VRAM IS READ VIA NVML (`nvidia-smi`), NEVER `total - memory_allocated()`.
The latter sees only the calling process, and would report the card empty while
a worker subprocess holds 18 GB of it — which is the whole thing being measured
here.

THE CARD THIS RUNS ON IS PROBABLY NOT THE CARD THIS IS FOR. The design target
moved from 24 GB to ~32 GB on 2026-08-17, because co-residency is what buys the
10-20 s conversion and 24 GB cannot hold Gemma and the audio models together.
On anything larger than the target, a peak that fits is an UPPER BOUND rather
than a demonstration — so the peak is printed against the target explicitly and
the verdict says which it is.
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

#: The card this is designed for, regardless of what is actually installed.
#: 32 GB since 2026-08-17: Gemma 19.2 + VoxCPM 5.4 + OmniVoice 4.7 ~= 29.3 GB
#: plus headroom, all resident. See `app/inference/capacity.py`.
DESIGN_TARGET_MIB = 32768

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

        # Warm Gemma explicitly, as the app lifespan now does, so the loop
        # below measures conversions rather than one conversion plus a load.
        t0 = time.time()
        load_sec = await transliterator.warm()
        gemma_mib = used_mib()
        print(f"[2] after warming Gemma: {gemma_mib} MiB "
              f"(+{gemma_mib - warm_mib}, {time.time() - t0:.0f}s, load {load_sec:.0f}s)")
        if gemma_mib < warm_mib:
            failures.append(
                "warming Gemma REDUCED total VRAM — the audio model was evicted, "
                "which reserve_slot must never do"
            )

        peaks: list[int] = []
        walls: list[float] = []
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
            walls.append(elapsed)
            print(f"    out: {result.text}")
            print(f"    load {result.load_time_sec:.1f}s | gen {result.gen_time_sec:.1f}s "
                  f"| wall {elapsed:.0f}s")
            print(f"    peak {sampler.peak} MiB | after {after} MiB")

            if not result.text.strip():
                failures.append(f"{source}->{target}: empty output")
            # Gemma must STILL BE THERE. The inverted assertion: this used to
            # fail if VRAM had not been released, and now fails if it has.
            if after < warm_mib:
                failures.append(
                    f"{source}->{target}: VRAM fell to {after} MiB — something "
                    f"was evicted (audio alone was {warm_mib})"
                )
            # And every conversion after the first must be GENERATION ONLY.
            if len(walls) > 1 and elapsed > 30:
                failures.append(
                    f"{source}->{target}: took {elapsed:.0f}s with the model "
                    f"already resident — it reloaded when it should not have"
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
