"""
Is there room on this card for the transliterator, and if not, what do we tell
people?

**Nothing here ever stops the app from starting.** A deployment on a card too
small for Gemma's ~19 GB must still enroll voices, run Speech Direction, and
generate audio in every language it already supported — the transliterator is
one feature, not a prerequisite. What it gets instead is an honest reason,
surfaced in the API and rendered in the UI, so "why is Convert greyed out"
has an answer that is not "it broke".

That is golden rule 5 one level up from audio: no silent fallback, and no
silent absence either. The failure this replaces is the one where an
over-committed card does not crash but *thrashes* — the transliterator
evicting audio models it then reloads, every conversion paying 150–330 s for
5 s of work, and the whole app merely looking slow rather than misconfigured.

THE ARITHMETIC
---------------
Three things want VRAM and only one goes through `InferenceScheduler`'s budget:

    budget_mb                     the audio models the scheduler manages
    gemma_transliterator_reserve  ~19.2 GB, resident (measured peak 19221 MiB)
    qwen_analyzer_reserve         ~6 GB, resident

The two reserves sit outside the budget on purpose — neither is a `RuntimeKind`
and neither may be reachable from `resolve()`. That is a routing decision with
a VRAM consequence, and until now the consequence was simply unaccounted:
`analyzer_scheduler.py`'s own docstring admits Qwen fits "in the slack" and
calls that a risk rather than a design. Writing the arithmetic down is what
turns it into one.

NO TORCH (golden rule 2). This is imported from `app.main`, so VRAM comes from
`nvidia-smi` over a subprocess, never `torch.cuda`.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

__all__ = ["CapacityReport", "total_vram_mb", "check_capacity"]

logger = logging.getLogger("app.inference.capacity")

#: Slack for CUDA context, allocator fragmentation and activation peaks. Not a
#: guess: Gemma's measured *peak* of 19221 MiB is already ~1.2 GB above its
#: 18 GB of weights, and the audio runtimes spike similarly during synthesis.
HEADROOM_MB = 2048


@dataclass(frozen=True, slots=True)
class CapacityReport:
    """What was needed, what was there, and whether it fit."""

    total_mb: int
    required_mb: int
    parts: tuple[tuple[str, int], ...]

    @property
    def unknown(self) -> bool:
        """No GPU, no driver, or no `nvidia-smi` — every CPU-only test run."""
        return self.total_mb == 0

    @property
    def fits(self) -> bool:
        """
        Unknown counts as FITS, deliberately.

        A machine that cannot be measured must not have a feature disabled on
        a guess. The runtime will fail with its own clear error if it really
        cannot load, and that error names the actual problem rather than one
        this module inferred.
        """
        return self.unknown or self.required_mb <= self.total_mb

    def shortfall_mb(self) -> int:
        return max(0, self.required_mb - self.total_mb) if not self.unknown else 0

    def reason(self) -> str:
        """
        One sentence, written for the person looking at a greyed-out button —
        not for a log. It says what is needed, what exists, and what to do,
        because "insufficient VRAM" alone leaves someone with no next step.
        """
        breakdown = ", ".join(f"{name} {mb} MiB" for name, mb in self.parts)
        return (
            f"Script conversion needs about {self.required_mb} MiB of GPU memory "
            f"({breakdown}) and this GPU has {self.total_mb} MiB — "
            f"{self.shortfall_mb()} MiB short. Everything else still works; "
            f"a card of about 32 GB or more enables it."
        )


def total_vram_mb() -> int:
    """
    Total VRAM of GPU 0, in MiB. `0` when it cannot be determined.

    Reads the WHOLE CARD via NVML rather than anything process-local — the trap
    CLAUDE.md records is `total - memory_allocated()`, which sees only the
    calling process and cheerfully reports 24 GB free while another process
    holds 20 GB of it. Every worker here IS another process.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        logger.debug("could not read total VRAM; capacity is treated as unknown")
        return 0


def check_capacity(
    *,
    budget_mb: int,
    transliterator_reserve_mb: int = 0,
    analyzer_reserve_mb: int = 0,
) -> CapacityReport:
    """
    Add up what will be resident and compare it to the card.

    Reserves are passed as 0 when that component is not configured — a
    deployment with no `.venv-gemma` genuinely does not need Gemma's 19 GB, and
    charging it for one it will never load would disable features on a card
    that is perfectly fine.
    """
    parts = [("audio budget", budget_mb)]
    if transliterator_reserve_mb:
        parts.append(("script conversion", transliterator_reserve_mb))
    if analyzer_reserve_mb:
        parts.append(("direction analyzer", analyzer_reserve_mb))
    parts.append(("headroom", HEADROOM_MB))
    return CapacityReport(
        total_mb=total_vram_mb(),
        required_mb=sum(mb for _, mb in parts),
        parts=tuple(parts),
    )
