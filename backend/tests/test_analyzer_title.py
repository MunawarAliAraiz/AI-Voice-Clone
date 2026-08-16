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


def test_row_count_mismatch_still_raises() -> None:
    raw = json.dumps({"title": "Fine title", "rows": _rows(1)})
    with pytest.raises(RuntimeError, match="expected 3"):
        _parse_and_validate(raw, 3)
