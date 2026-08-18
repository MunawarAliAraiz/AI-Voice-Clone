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
    _DEVANAGARI_TO_ROMAN,
    _DEVANAGARI_TO_URDU,
    _EXEMPLARS,
    _LATIN_EXEMPLARS,
    _URDU_TO_ROMAN,
    SOURCE_ARABIC,
    SOURCE_DEVANAGARI,
    SOURCE_LATIN,
    TARGET_PERSO_ARABIC,
    TARGET_ROMAN,
    build_system_prompt,
)


def test_the_latin_prompt_is_the_one_a3_passed_on() -> None:
    prompt = build_system_prompt()
    assert "You convert Roman Urdu into Perso-Arabic Urdu script." in prompt
    assert "Roman: " in prompt
    assert "Hindi: " not in prompt


def test_the_devanagari_to_roman_prompt_names_both_ends() -> None:
    prompt = build_system_prompt(
        source_script=SOURCE_DEVANAGARI, target_script=TARGET_ROMAN
    )
    assert "Devanagari" in prompt
    assert "Hindi: " in prompt
    assert "Roman: " in prompt          # the OUTPUT side of every example
    assert "Perso-Arabic" not in prompt  # not this conversion's business


def test_the_devanagari_to_urdu_prompt_is_a_different_prompt() -> None:
    """Same source, different target, and the exemplars differ accordingly —
    which is why the table is keyed on the PAIR and not on the source."""
    roman = build_system_prompt("", SOURCE_DEVANAGARI, TARGET_ROMAN)
    urdu = build_system_prompt("", SOURCE_DEVANAGARI, TARGET_PERSO_ARABIC)
    assert roman != urdu
    assert "Urdu: " in urdu


def test_the_perso_arabic_to_roman_prompt_reads_the_latin_set_backwards() -> None:
    prompt = build_system_prompt(source_script=SOURCE_ARABIC, target_script=TARGET_ROMAN)
    for roman, urdu in _LATIN_EXEMPLARS:
        assert f"Urdu: {urdu}" in prompt
        assert f"Roman: {roman}" in prompt


def _has(text: str, lo: str, hi: str) -> bool:
    return any(lo <= c <= hi for c in text)


def test_no_prompt_shows_a_script_its_conversion_is_not_about() -> None:
    """
    The property that matters, and NOT "each pair has unique strings" — the
    sets are derived from six shared sentences, so the Roman sentences appear
    in three of the four prompts by design. What must never happen is a prompt
    demonstrating a script that has nothing to do with its conversion, because
    that is what teaches a model to emit the wrong one.
    """
    devanagari_to_roman = build_system_prompt(
        source_script=SOURCE_DEVANAGARI, target_script=TARGET_ROMAN
    )
    assert not _has(devanagari_to_roman, "؀", "ۿ"), "no Perso-Arabic in a Roman target"

    latin_to_urdu = build_system_prompt(source_script=SOURCE_LATIN)
    assert not _has(latin_to_urdu, "ऀ", "ॿ"), "no Devanagari in the Roman->Urdu hop"

    urdu_to_roman = build_system_prompt(
        source_script=SOURCE_ARABIC, target_script=TARGET_ROMAN
    )
    assert not _has(urdu_to_roman, "ऀ", "ॿ"), "no Devanagari in the Urdu->Roman hop"


def test_the_devanagari_sources_appear_only_where_devanagari_is_the_source() -> None:
    latin = build_system_prompt(source_script=SOURCE_LATIN)
    devanagari = build_system_prompt(
        source_script=SOURCE_DEVANAGARI, target_script=TARGET_ROMAN
    )
    for src, _ in _DEVANAGARI_TO_ROMAN:
        assert src in devanagari
        assert src not in latin


def test_nothing_new_was_authored_for_any_derived_set() -> None:
    """
    The safety property behind deriving every set from the corpus's six
    sentences: every string in every prompt is one A3 run 3 passed on by ear.
    Authoring new gold would put unreviewed text into the prompt itself, where
    an error teaches the model the error rather than merely scoring badly.
    """
    romans = [r for r, _ in _LATIN_EXEMPLARS]
    urdus = [u for _, u in _LATIN_EXEMPLARS]
    assert [out for _, out in _DEVANAGARI_TO_ROMAN] == romans
    assert [out for _, out in _DEVANAGARI_TO_URDU] == urdus
    assert [(src, out) for src, out in _URDU_TO_ROMAN] == list(zip(urdus, romans))


def test_the_two_devanagari_sets_share_their_inputs() -> None:
    """Same six Devanagari sentences, two destinations. If these drifted, one
    target would be demonstrating cases the other never saw."""
    assert [s for s, _ in _DEVANAGARI_TO_ROMAN] == [s for s, _ in _DEVANAGARI_TO_URDU]


def test_an_unknown_pair_falls_back_to_the_gated_one_rather_than_raising() -> None:
    """The caller detected the source from the text, so an unexpected value
    means something like MIXED. Roman Urdu -> Perso-Arabic is the only
    conversion here that has passed a gate, so that is where it degrades to."""
    assert build_system_prompt(source_script="klingon") == build_system_prompt()
    # latin -> roman is a no-op with no prompt behind it; it degrades too.
    assert build_system_prompt(
        source_script=SOURCE_LATIN, target_script=TARGET_ROMAN
    ) == build_system_prompt()


def test_every_supported_pair_has_a_rule_one_and_a_name() -> None:
    """A pair in the table with no target wording would raise a KeyError deep
    inside a 19 GB model load rather than here."""
    for source, target in _EXEMPLARS:
        prompt = build_system_prompt("", source, target)
        assert prompt.startswith("You convert ")
        assert "\n1. " in prompt


def test_the_user_instruction_lands_after_the_rules_not_before() -> None:
    """
    Rule 3 ("do not translate, explain, summarise") is what stands between
    this feature and a model that answers the text. An addition placed above
    it could displace it; placed below and framed as a preference, it cannot.
    """
    prompt = build_system_prompt("Use Karachi slang.", SOURCE_DEVANAGARI, TARGET_ROMAN)
    assert prompt.index("Use Karachi slang.") > prompt.index("Do not translate")
    assert "only where it does not conflict" in prompt


def test_the_devanagari_set_demonstrates_the_danda() -> None:
    """
    `।` U+0964 has no counterpart in the target and must become `۔`. It is the
    one hard case with no Roman equivalent — SMS orthography with dropped
    vowels does not exist in Devanagari — so it takes that slot.
    """
    assert any("।" in src for src, _ in _DEVANAGARI_TO_ROMAN)
    assert all("।" not in out for _, out in _DEVANAGARI_TO_ROMAN)
    assert all("।" not in out for _, out in _DEVANAGARI_TO_URDU)
