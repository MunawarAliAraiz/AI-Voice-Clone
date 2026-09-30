"""Text helpers preserve ordering, revisions and local validation boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.inference.protocol import AnalyzeResult, TransliterateResult
from app.inference.remote_features import RemoteAnalyzer, RemoteTransliterator
from app.remote_worker.main import create_worker_app
from app.remote_worker.model_pins import AUXILIARY_PINS
from tests.fakes import FakeScheduler


class Analyzer:
    async def classify(self, *, language, sentences):
        assert language == "en"
        return AnalyzeResult(
            rows=tuple({"index": i, "emotion": "neutral"} for i, _ in enumerate(sentences)),
            title="Two lines",
            gen_time_sec=1.5,
        )


class Transliterator:
    async def convert_many(self, *, texts, **kwargs):
        assert texts == ["aap kaise hain", "main theek hun"]
        return [
            TransliterateResult(text="آپ کیسے ہیں", gen_time_sec=2.0),
            TransliterateResult(text="میں ٹھیک ہوں", gen_time_sec=2.5),
        ]


def test_worker_helpers_auth_limits_and_pins(tmp_path: Path) -> None:
    app = create_worker_app(
        scheduler=FakeScheduler(),
        settings=Settings(data_dir=tmp_path),
        token="secret",
        analyzer=Analyzer(),
        transliterator=Transliterator(),
    )
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret"}
        assert (
            client.post("/v1/analyze", json={"language": "en", "sentences": ["One"]}).status_code
            == 401
        )
        result = client.post(
            "/v1/analyze", headers=headers, json={"language": "en", "sentences": ["One", "Two"]}
        ).json()
        assert result["title"] == "Two lines"
        assert result["revision"] == AUXILIARY_PINS[result["model_id"]][1]
        assert len(result["rows"]) == 2
        body = {
            "texts": ["aap kaise hain", "main theek hun"],
            "source_script": "latin",
            "target_script": "perso_arabic",
        }
        response = client.post("/v1/transliterate", headers=headers, json=body)
        assert response.status_code == 200
        assert [item["text"] for item in response.json()["results"]] == [
            "آپ کیسے ہیں",
            "میں ٹھیک ہوں",
        ]
        body["target_script"] = "roman"
        assert client.post("/v1/transliterate", headers=headers, json=body).status_code == 422


@pytest.mark.asyncio
async def test_desktop_adapters_preserve_helper_data() -> None:
    class Features:
        async def call(self, path, model_id, body):
            if path == "/v1/analyze":
                return {
                    "rows": [{"index": 0}],
                    "title": "Title",
                    "gen_time_sec": 1.0,
                    "load_time_sec": 2.0,
                }
            return {
                "results": [
                    {"text": text, "gen_time_sec": 1.0, "load_time_sec": 2.0 if index == 0 else 0.0}
                    for index, text in enumerate(body["texts"])
                ]
            }

    assert (
        await RemoteAnalyzer(Features()).classify(language="en", sentences=("One",))
    ).title == "Title"
    results = await RemoteTransliterator(Features()).convert_many(texts=["first", "second"])
    assert [r.text for r in results] == ["first", "second"]
    assert sum(r.load_time_sec for r in results) == 2.0
