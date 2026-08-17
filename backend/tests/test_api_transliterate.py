"""
`POST /api/text/transliterate` — Roman Urdu → Perso-Arabic, as a job.

No GPU and no Gemma: the `TransliteratorScheduler` is replaced by a double
that returns whatever the test wants. What is under test here is the ORDER
(convert, then validate) and what happens when validation says no — both of
which are decisions this layer owns and neither of which needs a 19 GB model
to exercise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.inference.protocol import TransliterateResult
from app.main import create_app
from tests.fakes import FakeScheduler

_ROMAN = "Assalam o alaikum, kya haal hai aap ka?"
_URDU = "السلام علیکم، کیا حال ہے آپ کا؟"


class _FakeTransliterator:
    """Stands in for `TransliteratorScheduler`. Never touches a GPU."""

    def __init__(self, text: str = _URDU, error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def convert(
        self,
        *,
        text: str,
        instruction: str = "",
        source_script: str = "latin",
        target_script: str = "perso_arabic",
    ) -> TransliterateResult:
        self.calls.append({
            "text": text, "instruction": instruction,
            "source_script": source_script, "target_script": target_script,
        })
        if self._error is not None:
            raise self._error
        return TransliterateResult(text=self._text, gen_time_sec=1.0, load_time_sec=78.4)


def _client(tmp_path: Path, transliterator: Any | None = None):
    app = create_app(scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path))
    fake = transliterator if transliterator is not None else _FakeTransliterator()
    # Injected before startup so the lifespan does not build a real one — and
    # `owns_transliterator` stays false, so the app does not shut down a double
    # it did not create. Same rule as the injected scheduler and db.
    app.state.transliterator = fake
    return TestClient(app), fake


def _poll(c: TestClient, job_id: int, *, max_polls: int = 200) -> dict[str, Any]:
    for _ in range(max_polls):
        body = c.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
    raise AssertionError(f"job {job_id} never settled")


def test_transliterate_enqueues_and_returns_the_converted_text(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": _ROMAN})
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["status"] in ("queued", "running")
        # No route: this never calls resolve() and is not in the audio catalog.
        assert job["route"] is None

        done = _poll(c, job["id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["text"] == _URDU
    assert done["result"]["source_text"] == _ROMAN
    assert fake.calls[0]["text"] == _ROMAN


def test_the_user_instruction_reaches_the_model(tmp_path: Path) -> None:
    """It is editable on purpose — which is exactly why the validator below
    cannot be turned off from the same request."""
    client, fake = _client(tmp_path)
    with client as c:
        r = c.post(
            "/api/text/transliterate",
            json={"text": _ROMAN, "instruction": "Keep Karachi slang."},
        )
        _poll(c, r.json()["id"])
    assert fake.calls[0]["instruction"] == "Keep Karachi slang."


def test_an_answer_instead_of_a_transliteration_FAILS_the_job(tmp_path: Path) -> None:
    """
    THE FAILURE MODE AN EDITABLE INSTRUCTION INTRODUCES. A model handed
    "kya haal hai aap ka?" can answer it instead of converting it, and the
    answer is fluent, confident, and wrong. Returning it with a warning
    attached would be golden rule 5's silent substitution one layer up from
    audio — so the job FAILS, carrying the validator's reason code.
    """
    client, _ = _client(
        tmp_path,
        _FakeTransliterator(text="Hello! I am doing very well, thank you for asking."),
    )
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": _ROMAN})
        done = _poll(c, r.json()["id"])

    assert done["status"] == "failed"
    assert done["error"]["code"] == "TRANSLITERATION_REJECTED"
    assert done["error"]["reason"] == "not_urdu_script"
    assert done["result"] is None, "a rejected conversion must not leak its text"


def test_an_echo_of_the_input_fails_with_its_own_reason(tmp_path: Path) -> None:
    """Distinct from an answer, because the user's next move differs: an echo
    means the instruction was ignored, prose means it was obeyed wrongly."""
    client, _ = _client(tmp_path, _FakeTransliterator(text=_ROMAN))
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": _ROMAN})
        done = _poll(c, r.json()["id"])
    assert done["status"] == "failed"
    assert "echoed" in done["error"]["detail"]


def test_a_correct_conversion_carries_the_validator_measurements(tmp_path: Path) -> None:
    """They ride along rather than being recomputed client-side — a second
    implementation of the same check is a second thing to drift."""
    client, _ = _client(tmp_path)
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": _ROMAN})
        done = _poll(c, r.json()["id"])
    assert done["result"]["arabic_share"] > 0.9
    assert done["result"]["load_time_sec"] == 78.4


def test_no_transliterator_is_refused_at_the_door_with_a_reason(tmp_path: Path) -> None:
    """
    A deployment with no transliterator must still run every other job kind —
    and must SAY WHY rather than accepting a job that will fail.

    503 at enqueue, not a queued-then-failed job: whether this server has a
    transliterator is a deployment fact settled at startup, so making the user
    wait in a queue to be told the feature does not exist here is a worse
    version of the same answer. The reason comes from `app.state` verbatim, so
    the UI can render exactly what the server decided.
    """
    app = create_app(scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path))
    app.state.transliterator = None
    app.state.transliterator_reason = "needs about 27548 MiB and this GPU has 24576 MiB"
    with TestClient(app) as c:
        r = c.post("/api/text/transliterate", json={"text": _ROMAN})
    assert r.status_code == 503
    assert r.json()["code"] == "TRANSLITERATOR_UNAVAILABLE"
    assert "24576 MiB" in r.json()["detail"], "the VRAM reason must reach the user"


def test_the_system_endpoint_reports_why_conversion_is_unavailable(
    tmp_path: Path,
) -> None:
    """Where the UI learns to grey the button out, and what to say next to it."""
    app = create_app(scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path))
    app.state.transliterator = None
    app.state.transliterator_reason = "not enough GPU memory on this card"
    with TestClient(app) as c:
        body = c.get("/api/system").json()
    assert body["script_conversion"]["available"] is False
    assert body["script_conversion"]["reason"] == "not enough GPU memory on this card"


def test_the_system_endpoint_reports_conversion_as_available(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        body = c.get("/api/system").json()
    assert body["script_conversion"]["available"] is True
    assert body["script_conversion"]["reason"] is None


def test_empty_text_is_rejected_at_the_schema(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        assert c.post("/api/text/transliterate", json={"text": ""}).status_code == 422


def test_text_over_the_ceiling_is_rejected_before_the_gpu_is_touched(
    tmp_path: Path,
) -> None:
    """A conversion holds the WHOLE GPU, so an oversized request has to be
    refused at the door rather than after evicting every audio model."""
    client, fake = _client(tmp_path)
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": "a" * 5000})
    assert r.status_code == 422
    assert fake.calls == []


_HINDI = "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।"


_ROMAN_OF_HINDI = "mujhe samajh nahi aa raha ke ye kaise hua."


def test_a_devanagari_source_defaults_to_the_roman_target(tmp_path: Path) -> None:
    """
    The exemplar set is chosen from the TEXT, and the default target is ROMAN
    — a caption YouTube's ASR guessed at is a draft, and the transcript path
    exists to let the owner EDIT it rather than to speak it unread.
    """
    client, fake = _client(tmp_path, _FakeTransliterator(text=_ROMAN_OF_HINDI))
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": _HINDI})
        assert r.status_code == 202
        done = _poll(c, r.json()["id"])
    assert done["status"] == "succeeded", done
    assert fake.calls[0]["source_script"] == "devanagari"
    assert fake.calls[0]["target_script"] == "roman"
    assert done["result"]["target_script"] == "roman"


def test_the_caller_may_ask_for_perso_arabic_from_devanagari(tmp_path: Path) -> None:
    """The one-hop route, for a caption good enough that nobody wants to edit
    it. The target is the caller's precisely because this is a choice about
    what they are about to do, not a fact about the text."""
    client, fake = _client(tmp_path)
    with client as c:
        r = c.post(
            "/api/text/transliterate",
            json={"text": _HINDI, "target": "perso_arabic"},
        )
        assert r.status_code == 202
        done = _poll(c, r.json()["id"])
    assert done["status"] == "succeeded", done
    assert fake.calls[0]["target_script"] == "perso_arabic"


def test_perso_arabic_source_converts_to_roman_for_reading(tmp_path: Path) -> None:
    """The rare video whose captions arrive in Urdu script. Without this the
    transcript panel would have nothing to offer for it at all."""
    client, fake = _client(tmp_path, _FakeTransliterator(text=_ROMAN))
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": _URDU})
        assert r.status_code == 202
        done = _poll(c, r.json()["id"])
    assert done["status"] == "succeeded", done
    assert fake.calls[0]["source_script"] == "arabic"
    assert fake.calls[0]["target_script"] == "roman"


def test_an_unsupported_pair_is_refused_at_enqueue_not_on_the_gpu(
    tmp_path: Path,
) -> None:
    """
    422 at the door, not a failed job. Roman Urdu to Roman Urdu is a no-op with
    no prompt behind it, and finding that out forty seconds into a 19 GB load
    would spend the whole GPU to report a typo.
    """
    client, fake = _client(tmp_path)
    with client as c:
        r = c.post(
            "/api/text/transliterate", json={"text": _ROMAN, "target": "roman"}
        )
    assert r.status_code == 422
    assert r.json()["code"] == "UNSUPPORTED_CONVERSION"
    assert fake.calls == []


def test_roman_urdu_still_takes_the_gated_speech_hop(tmp_path: Path) -> None:
    """The one conversion here that has passed a listening gate, unchanged by
    everything the other three added."""
    client, fake = _client(tmp_path)
    with client as c:
        r = c.post("/api/text/transliterate", json={"text": _ROMAN})
        done = _poll(c, r.json()["id"])
    assert done["status"] == "succeeded"
    assert fake.calls[0]["source_script"] == "latin"
    assert fake.calls[0]["target_script"] == "perso_arabic"
    assert done["result"]["source_script"] == "latin"


def test_the_client_cannot_choose_the_source_script(tmp_path: Path) -> None:
    """
    An extra `source_script` in the body is ignored, not honoured — unlike
    `target`, which IS the caller's. The asymmetry is the design: the source is
    a fact about the text, the target is a preference about what happens next.
    """
    client, fake = _client(tmp_path, _FakeTransliterator(text=_ROMAN_OF_HINDI))
    with client as c:
        r = c.post(
            "/api/text/transliterate",
            json={"text": _HINDI, "source_script": "latin"},
        )
        assert r.status_code == 202
        _poll(c, r.json()["id"])
    assert fake.calls[0]["source_script"] == "devanagari"
