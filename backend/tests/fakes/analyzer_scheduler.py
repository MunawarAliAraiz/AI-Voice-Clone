"""
Fake AnalyzerScheduler — real implementation, not a stub.

Substituted for `inference.analyzer_scheduler.AnalyzerScheduler` in handler
and HTTP tests, the same way `FakeScheduler` stands in for
`InferenceScheduler` — so `jobs/handlers/analyze_llm.py` and
`POST /api/direction/analyze-llm` can be tested with no GPU and no
subprocess, and so a test can assert on exactly what `classify()` was
called with (in particular: that a handler test proves the handler does NOT
re-segment its own params — it can only pass through what it was given).
"""

from __future__ import annotations

from app.inference.protocol import AnalyzeResult

__all__ = ["FakeAnalyzerScheduler"]


class FakeAnalyzerScheduler:
    """
    Canned classify() results, keyed by call order. Configure `rows_by_call`
    to return a specific row set per call, or leave it empty for a generic
    neutral classification of the right length.
    """

    def __init__(
        self,
        *,
        rows_by_call: list[list[dict]] | None = None,
        raise_on_classify: Exception | None = None,
        gen_time_sec: float = 0.05,
    ) -> None:
        self._rows_by_call = rows_by_call or []
        self.raise_on_classify = raise_on_classify
        self.gen_time_sec = gen_time_sec

        #: Every classify() call received, as (language, sentences) tuples.
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.shutdown_calls = 0
        #: Overridable per test; the real analyzer returns this in the same
        #: generation as the rows.
        self.title = "Fake Title"

    async def classify(self, *, language: str, sentences: tuple[str, ...]) -> AnalyzeResult:
        self.calls.append((language, sentences))
        if self.raise_on_classify is not None:
            raise self.raise_on_classify

        call_index = len(self.calls) - 1
        if call_index < len(self._rows_by_call):
            rows = self._rows_by_call[call_index]
        else:
            rows = [
                {"index": i, "emotion": "neutral", "intensity": "medium",
                 "energy": "medium", "rate": "normal"}
                for i in range(len(sentences))
            ]
        return AnalyzeResult(
            rows=tuple(rows), title=self.title, gen_time_sec=self.gen_time_sec,
            load_time_sec=0.0,
        )

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
