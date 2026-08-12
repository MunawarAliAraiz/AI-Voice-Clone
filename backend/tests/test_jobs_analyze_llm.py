"""
The `analyze_llm` job handler — proves it classifies exactly the sentences
it was handed and never re-segments its own params. `AnalyzeLlmParams` has
no `text` field at all (only `language`/`sentences`), so the handler is
structurally incapable of calling `analyze()`/`split_sentences()` itself;
this test additionally proves it BEHAVIORALLY, by checking `classify()`
received exactly the stored sentence list, unchanged.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.db.database import Database
from app.inference.catalog import CATALOG
from app.jobs.handlers.analyze_llm import run_analyze_llm
from app.jobs.types import JobContext, JobKind, JobRecord, JobStatus
from tests.fakes import FakeAnalyzerScheduler, FakeScheduler


async def test_run_analyze_llm_classifies_exactly_the_stored_sentences(tmp_path: Path) -> None:
    analyzer = FakeAnalyzerScheduler()
    db = Database(tmp_path / "test.db")
    await db.connect()
    settings = Settings(data_dir=tmp_path)
    ctx = JobContext(
        db=db, scheduler=FakeScheduler(), catalog=CATALOG, settings=settings, analyzer=analyzer,
    )

    # Deliberately not what a real analyze() over any plausible source text
    # would produce — if the handler ever called analyze()/split_sentences()
    # itself instead of trusting job.params, this would diverge from what
    # gets passed to classify().
    stored_sentences = ["Deliberately-not-a-real-sentence-boundary one.", "And two."]
    job = JobRecord(
        id=1, kind=JobKind.ANALYZE_LLM, status=JobStatus.RUNNING,
        params={"language": "en", "sentences": stored_sentences},
        route=None, history_id=None, result=None, error=None,
        profile_id=None, priority=0, cancel_requested=False, attempt=1,
        queued_at="2026-01-01T00:00:00Z", started_at=None, finished_at=None,
        updated_at="2026-01-01T00:00:00Z",
    )

    outcome = await run_analyze_llm(ctx, job)

    assert analyzer.calls == [("en", tuple(stored_sentences))]
    assert outcome.history_id is None  # no audio, no generation_history row
    assert outcome.result is not None
    assert len(outcome.result["rows"]) == len(stored_sentences)
    assert outcome.result["rows"][0]["index"] == 0
    assert "gen_time_sec" in outcome.result and "load_time_sec" in outcome.result

    await db.close()


async def test_run_analyze_llm_empty_sentences_still_calls_classify(tmp_path: Path) -> None:
    """Even an empty list is passed through verbatim — no implicit re-derive."""
    analyzer = FakeAnalyzerScheduler()
    db = Database(tmp_path / "test.db")
    await db.connect()
    settings = Settings(data_dir=tmp_path)
    ctx = JobContext(
        db=db, scheduler=FakeScheduler(), catalog=CATALOG, settings=settings, analyzer=analyzer,
    )
    job = JobRecord(
        id=2, kind=JobKind.ANALYZE_LLM, status=JobStatus.RUNNING,
        params={"language": "ur", "sentences": []},
        route=None, history_id=None, result=None, error=None,
        profile_id=None, priority=0, cancel_requested=False, attempt=1,
        queued_at="2026-01-01T00:00:00Z", started_at=None, finished_at=None,
        updated_at="2026-01-01T00:00:00Z",
    )

    outcome = await run_analyze_llm(ctx, job)

    assert analyzer.calls == [("ur", ())]
    assert outcome.result is not None
    assert outcome.result["rows"] == []

    await db.close()
