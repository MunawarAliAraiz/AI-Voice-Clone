"""
AI Voice Clone Studio — Runtime backends (the torch side).

Everything under this package runs ONLY inside a worker subprocess spawned by
`inference/worker.py`. It is never imported by the API process — that is what
keeps `import torch` unreachable from `app.main` (enforced by
`tests/test_contracts.py::test_no_torch_outside_runtimes`, whose allowlist is
exactly `inference/runtimes/` + `inference/worker.py`).

A backend is a thin, SYNCHRONOUS object: load a checkpoint, render one request,
unload. No async, no routing, no transliteration — the worker hands it the final
text in the final script and it renders that or raises. All the concurrency and
eviction logic lives in the scheduler, on the far side of the wire protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["RuntimeBackend", "make_backend"]


@runtime_checkable
class RuntimeBackend(Protocol):
    """
    What `worker.py` drives. One backend per `RuntimeKind`.

    Methods are blocking and single-threaded by construction: the worker reads
    one request, calls one method, writes one response. Concurrency is the
    scheduler's problem, one process out.
    """

    #: The RuntimeKind value this backend serves (e.g. "voxcpm").
    runtime: str
    #: Currently loaded spec id, or None. Read by PING; never touches the GPU.
    loaded_model_id: str | None

    def load(self, model_id: str, hf_repo: str, hf_revision: str) -> float:
        """Load a checkpoint at a PINNED revision. Returns load seconds."""
        ...

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
        """
        Render one request to `output_path`. Returns metadata only —
        `{duration_sec, gen_time_sec, sample_rate}` — never a waveform.
        """
        ...

    def unload(self) -> None:
        """Drop the checkpoint, keep the process (and its CUDA context)."""
        ...


def make_backend(runtime: str) -> RuntimeBackend:
    """
    Construct the backend for a runtime. Imports are deferred to the branch so a
    worker only ever imports its own heavy stack.
    """
    if runtime == "voxcpm":
        from .voxcpm import VoxCPMBackend

        return VoxCPMBackend()
    if runtime == "chatterbox":
        from .chatterbox import ChatterboxBackend

        return ChatterboxBackend()
    if runtime == "fake":
        import os

        # Silence-only, but still gated: a fake runtime reachable in production
        # is how a demo mode becomes a shipped lie. It must be opted into.
        if os.environ.get("VCS_ALLOW_FAKE_RUNTIME") not in {"1", "true", "True"}:
            raise ValueError(
                "fake runtime requires VCS_ALLOW_FAKE_RUNTIME=1 in the environment"
            )
        from .fake import FakeBackend

        return FakeBackend()
    raise ValueError(f"no runtime backend for {runtime!r}")
