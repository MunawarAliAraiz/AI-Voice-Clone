"""
AI Voice Clone Studio — Production worker factory.

The scheduler depends on a `WorkerFactory` (RuntimeKind -> started WorkerHandle)
so its whole test suite can inject `FakeWorker` and run with no GPU. This module
supplies the REAL factory the app wires in at startup: it constructs a
`WorkerProcess`, starts it (awaiting the READY handshake), and hands it back.

No torch here — a factory only *spawns* the subprocess that imports torch; it
never imports it. Each runtime maps to its own interpreter (its own venv), which
is the entire reason workers are subprocesses rather than threads: F5, Chatterbox
and VoxCPM pin dependency stacks that are not expected to co-resolve.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from .protocol import WorkerHandle
from .spec import RuntimeKind
from .worker_client import WorkerProcess

__all__ = ["WorkerFactory", "make_worker_factory"]

WorkerFactory = Callable[[RuntimeKind], Awaitable[WorkerHandle]]


def make_worker_factory(
    *,
    interpreters: dict[RuntimeKind, str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> WorkerFactory:
    """
    Build the factory the scheduler calls to spawn a worker for a runtime.

    Args:
        interpreters: RuntimeKind -> absolute path of that runtime's venv python.
            A runtime with no entry is unrunnable and raises when requested,
            rather than silently falling back to the wrong interpreter.
        env: full environment for every worker (cache redirects like HF_HOME,
            plus PATH). Replaces the process environment, so pass a complete map.
        cwd: working directory workers start in — the dir from which
            `app.inference.worker` is importable.
    """

    async def factory(runtime: RuntimeKind) -> WorkerHandle:
        interp = interpreters.get(runtime)
        if interp is None:
            raise ValueError(f"no interpreter configured for runtime {runtime.value!r}")
        worker = WorkerProcess(runtime.value, interp, env=env, cwd=cwd)
        await worker.start()
        return worker

    return factory
