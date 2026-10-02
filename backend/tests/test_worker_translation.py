"""Published worker explicitly distinguishes draft translation from Urdu respelling."""

import pytest
from app.config import Settings
from app.domain.transliterate import TransliterationRejected, validate_translation
from app.inference.protocol import TransliterateResult
from app.inference.runtimes.gemma_transliterator import build_translation_prompt
from app.remote_worker.main import create_worker_app
from fastapi.testclient import TestClient

from tests.fakes import FakeScheduler


@pytest.fixture
def worker(tmp_path):
    class Translator:
        def __init__(self):
            self.calls = []

        async def convert_many(self, **kwargs):
            self.calls.append(kwargs)
            return [TransliterateResult(text="Aaj mausam acha hai.", gen_time_sec=1)]

    translator = Translator()
    app = create_worker_app(
        settings=Settings(data_dir=tmp_path),
        scheduler=FakeScheduler(),
        token="isolated-worker-token",  # noqa: S106 -- isolated test credential
        transliterator=translator,
    )
    with TestClient(app) as client:
        yield client, translator


@pytest.mark.parametrize("language", ["en", "hi"])
@pytest.mark.parametrize("target", ["roman", "perso_arabic"])
def test_explicit_translation_language_is_forwarded_and_echoed(worker, language, target):
    client, translator = worker
    result = client.post(
        "/v1/transliterate",
        headers={"Authorization": "Bearer isolated-worker-token"},
        json={
            "texts": ["The weather is good."],
            "source_script": "latin",
            "target_script": target,
            "source_language": language,
        },
    )
    assert result.status_code == 200
    assert result.json()["source_language"] == language
    assert translator.calls[0]["source_language"] == language
    assert translator.calls[0]["target_script"] == target


@pytest.mark.parametrize(
    "language,target", [(None, "roman"), ("ur", "roman"), ("fr", "roman"), ("en", "devanagari")]
)
def test_invalid_translation_and_legacy_latin_roman_are_refused(worker, language, target):
    client, translator = worker
    result = client.post(
        "/v1/transliterate",
        headers={"Authorization": "Bearer isolated-worker-token"},
        json={
            "texts": ["Some source."],
            "source_script": "latin",
            "target_script": target,
            "source_language": language,
        },
    )
    assert result.status_code == 422
    assert translator.calls == []


def test_legacy_urdu_script_conversion_preserves_existing_runtime_contract(worker):
    client, translator = worker
    result = client.post(
        "/v1/transliterate",
        headers={"Authorization": "Bearer isolated-worker-token"},
        json={
            "texts": ["Aaj mausam acha hai."],
            "source_script": "latin",
            "target_script": "perso_arabic",
        },
    )
    assert result.status_code == 200
    assert result.json()["source_language"] is None
    assert "source_language" not in translator.calls[0]


def test_translation_prompt_and_validator_do_not_claim_semantic_quality():
    prompt = build_translation_prompt("en", "roman")
    assert "Translate its meaning" in prompt and "not an English translation" in prompt
    assert "Hindi" in build_translation_prompt("hi", "perso_arabic")
    with pytest.raises(ValueError):
        build_translation_prompt("fr", "roman")
    with pytest.raises(TransliterationRejected):
        validate_translation("English source", "English source", "roman")
    with pytest.raises(TransliterationRejected):
        validate_translation("English source", "An English response", "perso_arabic")
    assert validate_translation("The weather is good.", "آج موسم اچھا ہے۔", "perso_arabic")
