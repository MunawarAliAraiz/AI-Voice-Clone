"""
The title the analyzer produces alongside its prosody rows.

Pure parser/validator tests — no torch, no GPU. `_parse_and_validate` lives in
the worker-side runtime module, which imports torch only INSIDE `load()`, so
importing it here is safe and `tests/test_contracts.py`'s no-torch rule is not
weakened (nothing at module scope touches it).
"""

from __future__ import annotations

import json

import pytest

from app.inference.runtimes.qwen_analyzer import _parse_and_validate, _validate_title


def _rows(n: int) -> list[dict[str, object]]:
    return [
        {"index": i, "emotion": "neutral", "intensity": "medium",
         "energy": "medium", "rate": "normal"}
        for i in range(n)
    ]


def test_title_and_rows_come_back_from_one_response() -> None:
    """The whole point of putting the title in the rows call: one generation,
    one worker round-trip, one ~6 GB model load — not two."""
    raw = json.dumps({"title": "Office message", "rows": _rows(2)})
    title, rows = _parse_and_validate(raw, 2)
    assert title == "Office message"
    assert len(rows) == 2


def test_title_survives_a_model_that_wraps_the_json_in_chatter() -> None:
    payload = json.dumps({"title": "Late again", "rows": _rows(1)})
    raw = f"Sure! Here you go:\n{payload}\nHope that helps."
    title, rows = _parse_and_validate(raw, 1)
    assert title == "Late again"
    assert len(rows) == 1


def test_internal_whitespace_is_collapsed() -> None:
    assert _validate_title("  Office   message \n") == "Office message"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "\n\t ",
        "one two three four five six",           # too many words
        "x" * 61,                                 # too many chars
    ],
)
def test_bad_titles_raise_rather_than_being_trimmed(bad: str) -> None:
    """
    Strict, exactly like the rows. Repairing a bad title here would hide that a
    prompt or model change had broken this path — the API layer decides what to
    fall back to, this layer only reports.
    """
    with pytest.raises(RuntimeError):
        _validate_title(bad)


@pytest.mark.parametrize("bad", [None, 42, ["Office", "message"], {"a": 1}])
def test_non_string_titles_raise(bad: object) -> None:
    with pytest.raises(RuntimeError):
        _validate_title(bad)


def test_missing_title_key_raises() -> None:
    raw = json.dumps({"rows": _rows(1)})
    with pytest.raises(RuntimeError, match="title"):
        _parse_and_validate(raw, 1)


def test_a_bare_array_is_now_rejected() -> None:
    """The pre-title response shape. It must fail loudly rather than yielding
    rows with no title, so a stale worker build cannot half-work."""
    with pytest.raises(RuntimeError):
        _parse_and_validate(json.dumps(_rows(1)), 1)


def test_row_validation_still_applies_alongside_the_title() -> None:
    raw = json.dumps({
        "title": "Fine title",
        "rows": [{"index": 0, "emotion": "ecstatic", "intensity": "medium",
                  "energy": "medium", "rate": "normal"}],
    })
    with pytest.raises(RuntimeError, match="emotion"):
        _parse_and_validate(raw, 1)


def test_the_models_missing_final_brace_is_completed() -> None:
    """
    REGRESSION (2026-08-17), verbatim from the pod — this exact string is what
    Qwen2.5-3B returns. It closes the rows array and stops, omitting the
    object's own final `}`. Byte-identical at max_new_tokens 300 and 900 under
    greedy decoding, so the model is choosing to stop, not being cut off.

    Every title was the text fallback because of this one character. The old
    `rfind("}")` extraction made it unreadable on top: it landed on the LAST
    ROW's closing brace, discarding the `]` too, so the failure surfaced as
    `Expecting ',' delimiter: line 6 column 93` — pointing into the middle of
    a perfectly valid array.
    """
    raw = (
        '{\n"title": "Greeting",\n"rows": [\n'
        '{"index": 0, "emotion": "neutral", "intensity": "medium", '
        '"energy": "medium", "rate": "normal"},\n'
        '{"index": 1, "emotion": "neutral", "intensity": "low", '
        '"energy": "medium", "rate": "normal"},\n'
        '{"index": 2, "emotion": "neutral", "intensity": "low", '
        '"energy": "medium", "rate": "normal"}\n]'
    )
    title, rows = _parse_and_validate(raw, 3)
    assert title == "Greeting"
    assert len(rows) == 3


def test_completion_cannot_rescue_a_genuinely_incomplete_response() -> None:
    """
    The line that keeps the completion above honest. Closing brackets are
    appended for what was watched open — nothing else — so a response cut off
    mid-row still fails, because the row it was writing is not valid JSON no
    matter what is appended after it.
    """
    raw = (
        '{"title": "Greeting", "rows": [{"index": 0, "emotion": "neut'
    )
    with pytest.raises(RuntimeError):
        _parse_and_validate(raw, 1)


def test_completion_does_not_invent_a_missing_row() -> None:
    """A completed object is still fully validated afterwards: the row count,
    every enum, every required key. Completion buys parseability, never a pass."""
    raw = '{"title": "Greeting", "rows": [' + json.dumps(_rows(1)[0])
    with pytest.raises(RuntimeError, match="expected 3"):
        _parse_and_validate(raw, 3)


def test_a_brace_inside_a_title_does_not_end_the_object() -> None:
    """
    Titles are model-written free text, so a `{` or `}` in one is structure
    only if you are not tracking strings. Depth-matching has to be
    quote-aware or it stops early and reports garbage.
    """
    raw = json.dumps({"title": "Braces } and { here", "rows": _rows(1)})
    title, rows = _parse_and_validate(raw, 1)
    assert title == "Braces } and { here"
    assert len(rows) == 1


def test_trailing_prose_after_the_object_is_still_ignored() -> None:
    """Depth-matching must not lose what `rfind` got right: a model that keeps
    talking after the JSON is fine, and its chatter can itself contain braces."""
    payload = json.dumps({"title": "Office message", "rows": _rows(2)})
    title, rows = _parse_and_validate(f"{payload}\nHope that helps! {{not json}}", 2)
    assert title == "Office message"
    assert len(rows) == 2


def test_row_count_mismatch_still_raises() -> None:
    raw = json.dumps({"title": "Fine title", "rows": _rows(1)})
    with pytest.raises(RuntimeError, match="expected 3"):
        _parse_and_validate(raw, 3)
