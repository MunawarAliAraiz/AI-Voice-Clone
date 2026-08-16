"""
`POST /api/text/title` — the short label the Composer puts on a generation.

Synchronous, unlike every other model-backed operation here, because it returns
two words and the client needs them before it can enqueue what it actually came
for. No GPU: `AnalyzerScheduler` is injected as a `FakeAnalyzerScheduler`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.exceptions import AnalyzerUnavailableError
from app.main import create_app
from tests.fakes import FakeAnalyzerScheduler, FakeScheduler


def _client(tmp_path: Path, analyzer: FakeAnalyzerScheduler | None = None):
    fake = analyzer or FakeAnalyzerScheduler()
    app = create_app(
        scheduler=FakeScheduler(), analyzer=fake, settings=Settings(data_dir=tmp_path)
    )
    return TestClient(app), fake


def test_title_comes_from_the_analyzer(tmp_path: Path) -> None:
    analyzer = FakeAnalyzerScheduler()
    analyzer.title = "دفتر کا پیغام"
    client, _ = _client(tmp_path, analyzer)
    with client as c:
        r = c.post("/api/text/title", json={
            "text": "میں دفتر جا رہا ہوں۔ آج دیر ہو گئی۔", "language": "ur",
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"title": "دفتر کا پیغام", "source": "analyzer"}


def test_an_unavailable_analyzer_falls_back_to_the_text(tmp_path: Path) -> None:
    """
    Cosmetic fallback, and the response SAYS so. Golden rules 1 and 5 govern
    audio and routing; a label on a list row is neither, and hiding which path
    produced it is what would actually be wrong.
    """
    analyzer = FakeAnalyzerScheduler()
    analyzer.raise_on_classify = AnalyzerUnavailableError("no interpreter configured")
    client, _ = _client(tmp_path, analyzer)
    with client as c:
        r = c.post("/api/text/title", json={
            "text": "one two three four five six seven", "language": "en",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "text"
        assert body["title"] == "one two three four", "first four words, not the whole text"


def test_a_crashing_analyzer_still_returns_a_title(tmp_path: Path) -> None:
    """A generation must never be blocked by the analyzer — that was the whole
    reason the title is not a hard gate on Generate."""
    analyzer = FakeAnalyzerScheduler()
    analyzer.raise_on_classify = RuntimeError("worker died mid-request")
    client, _ = _client(tmp_path, analyzer)
    with client as c:
        r = c.post("/api/text/title", json={"text": "hello there", "language": "en"})
        assert r.status_code == 200, r.text
        assert r.json() == {"title": "hello there", "source": "text"}


def test_a_blank_analyzer_title_falls_back(tmp_path: Path) -> None:
    analyzer = FakeAnalyzerScheduler()
    analyzer.title = "   "
    client, _ = _client(tmp_path, analyzer)
    with client as c:
        r = c.post("/api/text/title", json={"text": "hello there", "language": "en"})
        assert r.json()["source"] == "text"


def test_empty_text_is_refused(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        assert c.post("/api/text/title", json={"text": "", "language": "en"}).status_code == 422


def test_generate_persists_the_title_through_to_history(tmp_path: Path) -> None:
    """
    The title has to survive enqueue -> job params -> handler -> history row.
    A queued job already carries it, before any history row exists.
    """
    import io

    import numpy as np
    import soundfile as sf

    sched = FakeScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True)
    with TestClient(create_app(scheduler=sched, settings=settings)) as c:
        t = np.linspace(0, 1.5, int(16000 * 1.5), endpoint=False)
        buf = io.BytesIO()
        sf.write(buf, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 16000, format="WAV")
        pid = c.post(
            "/api/voices",
            files={"file": ("ref.wav", buf.getvalue(), "audio/wav")},
            data={"name": "v", "language": "ur", "consent": "true"},
        ).json()["id"]

        enqueued = c.post("/api/generate", json={
            "profile_id": pid, "text": "aap kaise hain", "language": "ur",
            "title": "Greeting note",
        })
        assert enqueued.status_code == 202, enqueued.text
        assert enqueued.json()["title"] == "Greeting note", "present while still queued"

        job_id = enqueued.json()["id"]
        for _ in range(200):
            poll = c.get(f"/api/jobs/{job_id}")
            if poll.json()["status"] in ("succeeded", "failed", "cancelled"):
                break
        assert poll.json()["status"] == "succeeded", poll.text
        assert poll.json()["title"] == "Greeting note"

        history = c.get("/api/history").json()
        assert history["items"][0]["title"] == "Greeting note"


def test_a_generation_without_a_title_is_allowed(tmp_path: Path) -> None:
    """Every row written before titles existed has none; nothing may require it."""
    import io

    import numpy as np
    import soundfile as sf

    sched = FakeScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True)
    with TestClient(create_app(scheduler=sched, settings=settings)) as c:
        t = np.linspace(0, 1.5, int(16000 * 1.5), endpoint=False)
        buf = io.BytesIO()
        sf.write(buf, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 16000, format="WAV")
        pid = c.post(
            "/api/voices",
            files={"file": ("ref.wav", buf.getvalue(), "audio/wav")},
            data={"name": "v", "language": "ur", "consent": "true"},
        ).json()["id"]

        r = c.post("/api/generate", json={
            "profile_id": pid, "text": "aap kaise hain", "language": "ur",
        })
        assert r.status_code == 202, r.text
        assert r.json()["title"] is None
