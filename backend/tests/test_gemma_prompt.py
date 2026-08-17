"""
The transliterator's PROMPT, tested without the model.

Importable in the API venv because `gemma_transliterator.py` imports torch
only inside `load()` — the same discipline every runtime follows, and the
reason `tests/test_contracts.py::test_no_torch_outside_runtimes` still passes
with this file present.

WHY A PROMPT IS WORTH TESTING AT ALL
-------------------------------------
Because it is the part of this feature with no other check on it. A wrong
threshold fails a test; a wrong exemplar just produces slightly worse Urdu
that nobody notices until a listening gate months later. The properties below
are the ones that would be silently wrong: the header naming one source while
the examples demonstrate another, the user's turn carrying a prefix the
exemplars never used, and the user's own instruction outranking rule 3.
"""

from __future__ import annotations

from app.inference.runtimes.gemma_transliterator import (
    _DEVANAGARI_EXEMPLARS,
    _LATIN_EXEMPLARS,
    SOURCE_DEVANAGARI,
    SOURCE_LATIN,
    build_system_prompt,
)


def test_the_latin_prompt_is_the_one_a3_passed_on() -> None:
    prompt = build_system_prompt()
    assert "You convert Roman Urdu into Perso-Arabic Urdu script." in prompt
    assert "Roman: " in prompt
    assert "Hindi: " not in prompt


def test_the_devanagari_prompt_names_devanagari_and_uses_its_own_examples() -> None:
    prompt = build_system_prompt(source_script=SOURCE_DEVANAGARI)
    assert "Devanagari" in prompt
    assert "Hindi: " in prompt
    # The header and the examples are ONE decision. A prompt that says "Hindi"
    # over six `Roman:` turns is worse than either half alone.
    assert "Roman: " not in prompt


def test_each_source_carries_only_its_own_exemplars() -> None:
    latin = build_system_prompt(source_script=SOURCE_LATIN)
    devanagari = build_system_prompt(source_script=SOURCE_DEVANAGARI)
    for src, _ in _LATIN_EXEMPLARS:
        assert src in latin
        assert src not in devanagari
    for src, _ in _DEVANAGARI_EXEMPLARS:
        assert src in devanagari
        assert src not in latin


def test_the_two_sets_teach_the_same_urdu() -> None:
    """
    The safety property behind deriving one set from the other: every Urdu
    string in the prompt is one A3 run 3 passed on by ear. Authoring six new
    gold strings would put unreviewed Urdu into the prompt itself, where an
    error teaches the model the error rather than merely scoring badly.
    """
    assert [urdu for _, urdu in _DEVANAGARI_EXEMPLARS] == [
        urdu for _, urdu in _LATIN_EXEMPLARS
    ]


def test_an_unknown_source_falls_back_to_latin_rather_than_raising() -> None:
    """The caller detected the script from the text, so an unexpected value
    means something like MIXED — and Roman Urdu is the gated source."""
    assert build_system_prompt(source_script="klingon") == build_system_prompt()


def test_the_user_instruction_lands_after_the_rules_not_before() -> None:
    """
    Rule 3 ("do not translate, explain, summarise") is what stands between
    this feature and a model that answers the text. An addition placed above
    it could displace it; placed below and framed as a preference, it cannot.
    """
    prompt = build_system_prompt("Use Karachi slang.", SOURCE_DEVANAGARI)
    assert prompt.index("Use Karachi slang.") > prompt.index("Do not translate")
    assert "only where it does not conflict" in prompt


def test_the_devanagari_set_demonstrates_the_danda() -> None:
    """
    `।` U+0964 has no counterpart in the target and must become `۔`. It is the
    one hard case with no Roman equivalent — SMS orthography with dropped
    vowels does not exist in Devanagari — so it takes that slot.
    """
    assert any("।" in src for src, _ in _DEVANAGARI_EXEMPLARS)
    assert all("।" not in urdu for _, urdu in _DEVANAGARI_EXEMPLARS)
