"""
`POST /api/transcript/prepare` — chunk pasted text for the Convert tab.

No network, no yt-dlp: the text comes straight from the caller. These assert the
contract the conversion UI depends on — a detected script, review-sized chunks
numbered contiguously, and preserved paragraph breaks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def _client(tmp_path: Path, **settings_kwargs: Any):
    app = create_app(
        scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path, **settings_kwargs)
    )
    return TestClient(app)


def _prepare(client: TestClient, text: str) -> dict[str, Any]:
    r = client.post("/api/transcript/prepare", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_roman_urdu_is_latin_and_routable(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        body = _prepare(c, "Aap kaise hain? Aaj mausam bohat acha hai.")
    assert body["script"] == "latin"
    assert body["needs_transliteration"] is False
    assert len(body["chunks"]) >= 1
    # Every chunk's text is present in the returned `text` — what the UI shows
    # and what it converts cannot drift apart.
    for chunk in body["chunks"]:
        assert chunk["text"] in body["text"]


def test_devanagari_is_flagged_as_needing_conversion(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        body = _prepare(c, "मेरा नाम अली है और मैं ठीक हूँ।")
    assert body["script"] == "devanagari"
    # No catalog cell renders Devanagari — the UI must be told to convert first.
    assert body["needs_transliteration"] is True


def test_urdu_script_is_arabic_and_routable(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        body = _prepare(c, "آپ کیسے ہیں؟ آج موسم بہت اچھا ہے۔")
    assert body["script"] == "arabic"
    assert body["needs_transliteration"] is False


def test_chunk_indexes_are_contiguous_from_zero(tmp_path: Path) -> None:
    """The UI keys per-part state and conversion results off `index`; a gap or a
    duplicate lands one part's conversion on another. Force several chunks with a
    tiny ceiling and assert 0..n-1 with no repeats."""
    long_text = " ".join(f"Yeh sentence number {i} hai." for i in range(20))
    with _client(tmp_path, transcript_chunk_chars=60, transcript_chunk_min_chars=20) as c:
        body = _prepare(c, long_text)
    indexes = [chunk["index"] for chunk in body["chunks"]]
    assert len(indexes) > 1
    assert indexes == list(range(len(indexes)))


def test_paragraph_breaks_are_preserved_as_pauses(tmp_path: Path) -> None:
    r"""A blank line the user typed is a real pause. It survives into `text` as a
    ``\n\n``, and no chunk straddles it."""
    with _client(tmp_path) as c:
        body = _prepare(c, "Pehla paragraph hai.\n\nDoosra paragraph hai.")
    assert "\n\n" in body["text"]
    for chunk in body["chunks"]:
        assert "\n\n" not in chunk["text"]


def test_single_paragraph_gets_no_injected_newline(tmp_path: Path) -> None:
    """One paragraph means no join, so nothing adds a newline the user did not
    type — each one is ~380 ms of real silence."""
    with _client(tmp_path) as c:
        body = _prepare(c, "Sirf ek line hai yahan.")
    assert "\n" not in body["text"]


def test_empty_text_is_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.post("/api/transcript/prepare", json={"text": ""})
    assert r.status_code == 422


def test_whitespace_only_text_does_not_crash(tmp_path: Path) -> None:
    """min_length=1 lets a lone space through the schema; the handler must not
    500 on it — it simply yields no chunks."""
    with _client(tmp_path) as c:
        r = c.post("/api/transcript/prepare", json={"text": "   "})
    assert r.status_code == 200
    assert r.json()["chunks"] == []
