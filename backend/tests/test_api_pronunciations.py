"""
CRUD for the user's pronunciation dictionary, plus the one thing that makes it
worth having: an entry created through the API changes what synthesis receives.

Against FakeScheduler — no torch, no GPU — but the real router, real database
and real `resolve()` run, so the last test is an end-to-end proof rather than a
check that a row was written.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def _client(tmp_path: Path, **kw: Any) -> tuple[TestClient, FakeScheduler]:
    sched = FakeScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True, **kw)
    return TestClient(create_app(scheduler=sched, settings=settings)), sched


def _wav_bytes(dur: float = 1.5, sr: int = 16000) -> bytes:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    buf = io.BytesIO()
    sf.write(buf, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, format="WAV")
    return buf.getvalue()


def test_pronunciation_crud_round_trip(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        assert c.get("/api/pronunciations").json() == {"items": [], "total": 0}

        r = c.post("/api/pronunciations", json={
            "key_text": "database", "replacement": "ڈیٹا بےس",
            "notes": "was read as data-boss",
        })
        assert r.status_code == 201, r.text
        entry = r.json()
        assert entry["language"] == "ur" and entry["is_enabled"] is True
        assert entry["notes"] == "was read as data-boss"

        assert c.get(f"/api/pronunciations/{entry['id']}").json()["key_text"] == "database"
        assert c.get("/api/pronunciations").json()["total"] == 1

        patched = c.patch(f"/api/pronunciations/{entry['id']}", json={"is_enabled": False})
        assert patched.status_code == 200, patched.text
        assert patched.json()["is_enabled"] is False
        assert patched.json()["replacement"] == "ڈیٹا بےس", "unsent fields must not change"

        assert c.delete(f"/api/pronunciations/{entry['id']}").status_code == 204
        assert c.get(f"/api/pronunciations/{entry['id']}").status_code == 404


def test_disabled_entries_are_listed_not_hidden(tmp_path: Path) -> None:
    """Hiding them would hide the only way to switch off a shipped default."""
    client, _ = _client(tmp_path)
    with client as c:
        c.post("/api/pronunciations", json={
            "key_text": "میٹنگ", "replacement": "unused", "is_enabled": False,
        })
        listed = c.get("/api/pronunciations").json()
        assert listed["total"] == 1
        assert listed["items"][0]["is_enabled"] is False


def test_duplicate_key_is_a_409_naming_the_existing_entry(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        first = c.post("/api/pronunciations", json={
            "key_text": "database", "replacement": "ڈیٹا بےس",
        }).json()

        # Different capitalisation is the SAME entry — matching is
        # case-insensitive, so two rows could never both apply.
        clash = c.post("/api/pronunciations", json={
            "key_text": "DataBase", "replacement": "something else",
        })
        assert clash.status_code == 409, clash.text
        assert clash.headers["content-type"].startswith("application/problem+json")
        body = clash.json()
        assert body["code"] == "PRONUNCIATION_CONFLICT"
        assert body["existing_id"] == first["id"], (
            "the client needs the id to offer 'edit the one you have'"
        )


def test_same_key_in_a_different_language_is_allowed(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        c.post("/api/pronunciations", json={"key_text": "database", "replacement": "ڈیٹا بےس"})
        r = c.post("/api/pronunciations", json={
            "key_text": "database", "replacement": "day-ta-base", "language": "en",
        })
        assert r.status_code == 201, r.text
        assert c.get("/api/pronunciations", params={"language": "ur"}).json()["total"] == 1


def test_renaming_an_entry_onto_another_key_is_a_409(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        c.post("/api/pronunciations", json={"key_text": "database", "replacement": "a"})
        second = c.post("/api/pronunciations", json={"key_text": "URL", "replacement": "b"}).json()

        clash = c.patch(f"/api/pronunciations/{second['id']}", json={"key_text": "database"})
        assert clash.status_code == 409, clash.text


def test_renaming_an_entry_to_its_own_key_is_not_a_conflict(tmp_path: Path) -> None:
    """A row must not collide with itself, or editing a replacement would 409."""
    client, _ = _client(tmp_path)
    with client as c:
        e = c.post("/api/pronunciations", json={"key_text": "database", "replacement": "a"}).json()
        r = c.patch(f"/api/pronunciations/{e['id']}", json={
            "key_text": "DATABASE", "replacement": "b",
        })
        assert r.status_code == 200, r.text
        assert r.json()["replacement"] == "b"


def test_missing_entry_is_a_404_problem(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        for r in (
            c.get("/api/pronunciations/999"),
            c.patch("/api/pronunciations/999", json={"replacement": "x"}),
            c.delete("/api/pronunciations/999"),
        ):
            assert r.status_code == 404, r.text
            assert r.json()["code"] == "PRONUNCIATION_NOT_FOUND"


def test_whitespace_only_and_oversized_input_is_refused(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        assert c.post("/api/pronunciations", json={
            "key_text": "   ", "replacement": "x",
        }).status_code == 422
        assert c.post("/api/pronunciations", json={
            "key_text": "x" * 101, "replacement": "y",
        }).status_code == 422


def test_surrounding_whitespace_is_collapsed_not_stored(tmp_path: Path) -> None:
    """
    A key with padding could never match — the matcher works on word boundaries
    — so storing it verbatim would create an entry that silently does nothing.
    """
    client, _ = _client(tmp_path)
    with client as c:
        e = c.post("/api/pronunciations", json={
            "key_text": "  pull   request ", "replacement": " پُل رِیکویسٹ ",
        }).json()
        assert e["key_text"] == "pull request"
        assert e["replacement"] == "پُل رِیکویسٹ"


def test_entry_created_through_the_api_changes_what_the_worker_receives(
    tmp_path: Path,
) -> None:
    """The whole point. Create an entry over HTTP, then generate, and assert
    the synthesized text — not the database row."""
    client, sched = _client(tmp_path)
    with client as c:
        r = c.post("/api/voices",
                   files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
                   data={"name": "v", "language": "ur", "consent": "true"})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]

        assert c.post("/api/pronunciations", json={
            "key_text": "دفتر", "replacement": "دَفتر",
        }).status_code == 201

        job = c.post("/api/generate", json={
            "profile_id": pid, "text": "میں دفتر جا رہا ہوں", "language": "ur",
            "model_id": "omnivoice_urdu",
        })
        assert job.status_code == 202, job.text
        for _ in range(200):
            poll = c.get(f"/api/jobs/{job.json()['id']}")
            if poll.json()["status"] in ("succeeded", "failed", "cancelled"):
                break
        assert poll.json()["status"] == "succeeded", poll.text
        assert sched.requests[-1].text == "میں دَفتر جا رہا ہوں"
