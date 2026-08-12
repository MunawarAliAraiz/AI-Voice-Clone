"""
`AnalyzerScheduler` semantics — lazy single start, load-once-then-classify
sequencing, idle-unload, and error mapping. No GPU, no real subprocess:
`WorkerProcess` is monkeypatched with `_FakeWorkerProcess`, a minimal double
implementing just the `call`/`start`/`kill`/`is_alive` surface
`AnalyzerScheduler` actually uses — deliberately not the shared `FakeWorker`
(that one is tuned for the audio scheduler's SYNTH/UNLOAD vocabulary; this
one only needs LOAD/CLASSIFY).
"""

from __future__ import annotations

import asyncio

import pytest

from app.exceptions import AnalyzerResponseInvalidError, AnalyzerUnavailableError
from app.inference.analyzer_scheduler import AnalyzerScheduler
from app.inference.protocol import WireOp, WireResponse


class _FakeWorkerProcess:
    """Stands in for `worker_client.WorkerProcess` in these tests."""

    def __init__(self, runtime: str, python_executable: str, *, env=None, cwd=None) -> None:
        self.runtime = runtime
        self.python_executable = python_executable
        self._alive = False
        self.load_calls = 0
        self.classify_calls: list[dict] = []
        self.start_calls = 0
        self.kill_calls = 0
        #: When set, returned verbatim from the next CLASSIFY call instead of
        #: the default canned success.
        self.classify_response: WireResponse | None = None
        self.fail_load = False
        #: When True, a CLASSIFY call also marks the worker dead — simulates
        #: a crash mid-request, distinct from a merely-invalid response.
        self.die_on_classify = False

    async def start(self) -> None:
        self.start_calls += 1
        self._alive = True

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def call(self, op: WireOp, payload: dict, *, timeout: float) -> WireResponse:
        if op == WireOp.LOAD:
            self.load_calls += 1
            if self.fail_load:
                return WireResponse(
                    id=0, ok=False, error_code="LOAD_FAILED", error_message="boom"
                )
            return WireResponse(id=0, ok=True, result={"load_time_sec": 0.01})
        if op == WireOp.CLASSIFY:
            self.classify_calls.append(payload)
            if self.die_on_classify:
                self._alive = False
                return WireResponse(
                    id=0, ok=False, error_code="WORKER_CRASHED", error_message="died"
                )
            if self.classify_response is not None:
                return self.classify_response
            sentences = payload["sentences"]
            rows = [
                {"index": i, "emotion": "neutral", "intensity": "medium",
                 "energy": "medium", "rate": "normal"}
                for i in range(len(sentences))
            ]
            return WireResponse(id=0, ok=True, result={"rows": rows, "gen_time_sec": 0.01})
        raise AssertionError(f"unexpected op in test double: {op}")

    async def kill(self, *, grace_sec: float = 5.0) -> None:
        self.kill_calls += 1
        self._alive = False


def _patched_scheduler(monkeypatch, **kwargs) -> tuple[AnalyzerScheduler, list[_FakeWorkerProcess]]:
    created: list[_FakeWorkerProcess] = []

    def factory(runtime: str, python_executable: str, *, env=None, cwd=None):
        w = _FakeWorkerProcess(runtime, python_executable, env=env, cwd=cwd)
        created.append(w)
        return w

    monkeypatch.setattr("app.inference.analyzer_scheduler.WorkerProcess", factory)
    scheduler = AnalyzerScheduler(python_executable="fake-python", **kwargs)
    return scheduler, created


async def test_no_interpreter_configured_raises_without_spawning(monkeypatch) -> None:
    _sched, created = _patched_scheduler(monkeypatch)
    scheduler = AnalyzerScheduler(python_executable="")
    with pytest.raises(AnalyzerUnavailableError):
        await scheduler.classify(language="en", sentences=("hi",))
    assert created == []  # never even tried to spawn


async def test_lazy_single_start_under_concurrent_callers(monkeypatch) -> None:
    scheduler, created = _patched_scheduler(monkeypatch)

    results = await asyncio.gather(
        scheduler.classify(language="en", sentences=("a", "b")),
        scheduler.classify(language="en", sentences=("c",)),
    )

    assert len(created) == 1, "two concurrent classify() calls spawned more than one worker"
    assert created[0].start_calls == 1
    assert created[0].load_calls == 1, "LOAD must happen exactly once, not once per call"
    assert len(results[0].rows) == 2
    assert len(results[1].rows) == 1


async def test_load_once_then_classify_sequencing(monkeypatch) -> None:
    scheduler, created = _patched_scheduler(monkeypatch)

    await scheduler.classify(language="en", sentences=("first",))
    await scheduler.classify(language="en", sentences=("second", "third"))

    worker = created[0]
    assert worker.load_calls == 1
    assert len(worker.classify_calls) == 2
    assert worker.classify_calls[0]["sentences"] == ["first"]
    assert worker.classify_calls[1]["sentences"] == ["second", "third"]


async def test_idle_unload_timer_kills_the_worker(monkeypatch) -> None:
    scheduler, created = _patched_scheduler(monkeypatch, idle_unload_sec=0.05)

    await scheduler.classify(language="en", sentences=("hi",))
    assert created[0].kill_calls == 0

    await asyncio.sleep(0.3)  # comfortably past the 0.05s idle window

    assert created[0].kill_calls >= 1, "idle worker was never killed"

    # A subsequent call must restart a fresh worker rather than reusing the
    # dead one — proves the scheduler actually forgot about it.
    await scheduler.classify(language="en", sentences=("hi again",))
    assert len(created) == 2

    await scheduler.shutdown()


async def test_error_mapping_invalid_response_keeps_worker_alive(monkeypatch) -> None:
    scheduler, created = _patched_scheduler(monkeypatch)
    await scheduler.classify(language="en", sentences=("warm up",))  # start + load

    created[0].classify_response = WireResponse(
        id=0, ok=False, error_code="ValueError", error_message="not valid json",
    )
    with pytest.raises(AnalyzerResponseInvalidError, match="not valid json"):
        await scheduler.classify(language="en", sentences=("bad",))

    assert created[0].is_alive, "a merely-invalid response must not be treated as a crash"


async def test_error_mapping_worker_death_raises_unavailable(monkeypatch) -> None:
    scheduler, created = _patched_scheduler(monkeypatch)
    await scheduler.classify(language="en", sentences=("warm up",))  # start + load

    created[0].die_on_classify = True
    with pytest.raises(AnalyzerUnavailableError):
        await scheduler.classify(language="en", sentences=("dying",))


async def test_load_failure_raises_unavailable(monkeypatch) -> None:
    created: list[_FakeWorkerProcess] = []

    # The first LOAD must fail, so the factory pre-configures `fail_load`
    # on construction rather than flipping it after the fact.
    def factory(runtime: str, python_executable: str, *, env=None, cwd=None):
        w = _FakeWorkerProcess(runtime, python_executable, env=env, cwd=cwd)
        w.fail_load = True
        created.append(w)
        return w

    monkeypatch.setattr("app.inference.analyzer_scheduler.WorkerProcess", factory)
    scheduler2 = AnalyzerScheduler(python_executable="fake-python")

    with pytest.raises(AnalyzerUnavailableError):
        await scheduler2.classify(language="en", sentences=("hi",))


async def test_shutdown_is_idempotent(monkeypatch) -> None:
    scheduler, created = _patched_scheduler(monkeypatch)
    await scheduler.classify(language="en", sentences=("hi",))

    await scheduler.shutdown()
    await scheduler.shutdown()  # must not raise

    assert created[0].kill_calls >= 1
