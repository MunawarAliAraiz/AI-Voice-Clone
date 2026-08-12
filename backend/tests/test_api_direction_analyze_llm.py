"""
`POST /api/direction/analyze-llm` — enqueues an `analyze_llm` job off a real
`analyze()` call, returns 202 in the same `JobStatusResponse` shape
`POST /generate` uses, and the job is pollable via the existing generic
`GET /api/jobs/{id}`. No GPU: `AnalyzerScheduler` is injected as a
`FakeAnalyzerScheduler`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.direction_analyze import analyze
from app.main import create_app
from tests.fakes import FakeAnalyzerScheduler, FakeScheduler


def _client(tmp_path: Path, analyzer: FakeAnalyzerScheduler | None = None):
    fake_analyzer = analyzer or FakeAnalyzerScheduler()
    settings = Settings(data_dir=tmp_path)
    app = create_app(scheduler=FakeScheduler(), analyzer=fake_analyzer, settings=settings)
    return TestClient(app), fake_analyzer


def _poll(c: TestClient, job_id: int, *, max_polls: int = 200) -> dict[str, Any]:
    for _ in range(max_polls):
        r = c.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {max_polls} polls")


def test_analyze_llm_returns_202_with_sentences_matching_a_fresh_analyze(tmp_path: Path) -> None:
    text = "I am so excited! Are you coming tomorrow?"
    client, analyzer = _client(tmp_path)
    with client as c:
        r = c.post("/api/direction/analyze-llm", json={"text": text, "language": "en"})
        assert r.status_code == 202, r.text
        body = r.json()
        assert isinstance(body["id"], int)
        assert body["kind"] == "analyze_llm"
        # Never touches resolve()/the audio catalog — no RoutePlan exists.
        assert body["route"] is None
        assert body["error"] is None

        expected_sentences = [seg.text for seg in analyze(text, "en").segments]

        finished = _poll(c, body["id"])
        assert finished["status"] == "succeeded"
        assert finished["route"] is None
        assert finished["result"]["rows"]
        assert len(finished["result"]["rows"]) == len(expected_sentences)

    # The analyzer was called with exactly the sentences a fresh analyze()
    # over the same text would produce — segmentation decided once, at
    # enqueue, never re-derived by the handler.
    assert len(analyzer.calls) == 1
    called_language, called_sentences = analyzer.calls[0]
    assert called_language == "en"
    assert list(called_sentences) == expected_sentences


def test_analyze_llm_job_is_pollable_via_generic_jobs_endpoint(tmp_path: Path) -> None:
    client, _analyzer = _client(tmp_path)
    with client as c:
        r = c.post(
            "/api/direction/analyze-llm",
            json={"text": "Aap kaise hain?", "language": "ur"},
        )
        assert r.status_code == 202
        job_id = r.json()["id"]

        # The same GET /api/jobs/{id} every other job kind uses — no
        # kind-specific polling endpoint was added.
        poll = c.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200
        assert poll.json()["kind"] == "analyze_llm"

        finished = _poll(c, job_id)
        assert finished["status"] == "succeeded"


def test_analyze_llm_appears_in_the_recent_jobs_list(tmp_path: Path) -> None:
    client, _analyzer = _client(tmp_path)
    with client as c:
        r = c.post(
            "/api/direction/analyze-llm", json={"text": "Test sentence.", "language": "en"}
        )
        job_id = r.json()["id"]
        _poll(c, job_id)

        lst = c.get("/api/jobs").json()
        item = next(i for i in lst["items"] if i["id"] == job_id)
        assert item["kind"] == "analyze_llm"
        assert item["route"] is None
