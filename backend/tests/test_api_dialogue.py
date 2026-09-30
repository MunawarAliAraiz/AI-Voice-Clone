"""Dialogue projects survive API restarts and refuse missing or fake clips."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def test_dialogue_draft_survives_new_desktop_session(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, api_key="first-key")
    draft = {
        "version": 1,
        "voices": {"1": 5, "2": None, "3": None},
        "lines": [{"id": "line-1", "text": "Hello", "attempts": []}],
    }
    with TestClient(create_app(scheduler=FakeScheduler(), settings=settings)) as client:
        assert client.get("/api/dialogue/draft").status_code == 401
        headers = {"X-API-Key": "first-key"}
        assert client.get("/api/dialogue/draft", headers=headers).json() == {"draft": None}
        assert client.put("/api/dialogue/draft", headers=headers, json={"draft": draft}).json() == {
            "saved": True
        }
        assert (
            client.put(
                "/api/dialogue/draft", headers=headers, json={"draft": {"version": 2}}
            ).status_code
            == 422
        )
    settings.api_key = "new-session-key"
    with TestClient(create_app(scheduler=FakeScheduler(), settings=settings)) as client:
        assert client.get(
            "/api/dialogue/draft", headers={"X-API-Key": "new-session-key"}
        ).json() == {"draft": draft}


def test_assembly_refuses_unknown_job_and_invalid_pause(tmp_path: Path) -> None:
    with TestClient(
        create_app(scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path))
    ) as client:
        assert client.post("/api/dialogue/assemble", json={"lines": []}).status_code == 422
        assert (
            client.post(
                "/api/dialogue/assemble", json={"lines": [{"job_id": 1, "pause_after_sec": -1}]}
            ).status_code
            == 422
        )
        assert (
            client.post("/api/dialogue/assemble", json={"lines": [{"job_id": 1}]}).status_code
            == 404
        )
