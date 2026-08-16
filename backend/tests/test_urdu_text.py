"""
Tests for domain.urdu_text — pure, no GPU, no torch, microseconds.

Numeral values mirror the ones eval/urdu_numerals.py verified against real
production OmniVoiceBackend audio (docs/URDU_BAKEOFF_RESULTS.md SS5c/SS5d),
so this asserts the ported module produces IDENTICAL output to what was
actually heard, not a re-derivation.
"""

from __future__ import annotations

from app.domain.urdu_text import TextNormalization, apply_text_normalizations


def test_expand_numbers_matches_eval_verified_values() -> None:
    cases = {
        "3": "تین",
        "14": "چودہ",
        "45": "پینتالیس",
        "2026": "دو ہزار چھبیس",
        "100": "ایک سو",
    }
    for digits, words in cases.items():
        text, applied = apply_text_normalizations(digits, (TextNormalization.NUMBERS,))
        assert text == words
        assert applied == (TextNormalization.NUMBERS,)


def test_expand_numbers_handles_eastern_arabic_indic_digits() -> None:
    text, applied = apply_text_normalizations(
        "۳ اور ۴۵", (TextNormalization.NUMBERS,)
    )
    assert text == "تین اور پینتالیس"
    assert applied == (TextNormalization.NUMBERS,)


def test_expand_numbers_is_a_no_op_on_digit_free_text() -> None:
    text, applied = apply_text_normalizations(
        "عام سا جملہ ہے", (TextNormalization.NUMBERS,)
    )
    assert text == "عام سا جملہ ہے"
    assert applied == ()


def test_loanword_lexicon_respells_url() -> None:
    text, applied = apply_text_normalizations(
        "یہ URL ہے۔", (TextNormalization.LOANWORD_LEXICON,)
    )
    assert text == "یہ یو آر ایل ہے۔"
    assert applied == (TextNormalization.LOANWORD_LEXICON,)


def test_loanword_lexicon_respells_database_with_bari_ye() -> None:
    # docs/URDU_BAKEOFF_RESULTS.md SS9c. Chosen by blind repeat sampling, not
    # one listen: `ڈیٹا بےس` scored 11/12 against the previously-shipped
    # `ڈیٹا base` at 7/12 and verbatim `database` at 0/4.
    #
    # Bari ye (U+06D2) carries the /eɪ/. The earlier all-Urdu `ڈیٹا بیس` was
    # rejected because بیس is also the Urdu word for "twenty"; `ڈیٹا base` was
    # rejected because a Latin `base` standing alone after Urdu text is often
    # read as "boss".
    text, applied = apply_text_normalizations(
        "ہمیں database چاہیے۔", (TextNormalization.LOANWORD_LEXICON,)
    )
    assert text == "ہمیں ڈیٹا بےس چاہیے۔"
    assert applied == (TextNormalization.LOANWORD_LEXICON,)


def test_loanword_lexicon_no_longer_emits_the_ambiguous_forms() -> None:
    """
    Regression guard for the two rejected spellings.

    Both were plausible enough to ship once -- `ڈیٹا base` actually did, on a
    single listen recorded as "verified" -- so this pins the distinction rather
    than trusting the entry not to drift back.
    """
    text, _ = apply_text_normalizations(
        "ہمیں database چاہیے۔", (TextNormalization.LOANWORD_LEXICON,)
    )
    assert "base" not in text, "Latin `base` alone is read as 'boss'"
    assert "بیس" not in text, "بیس is also the Urdu word for 'twenty'"


def test_loanword_lexicon_is_word_boundary_aware() -> None:
    """Must not corrupt a longer word that merely CONTAINS a lexicon entry."""
    text, applied = apply_text_normalizations(
        "urlencoded database123", (TextNormalization.LOANWORD_LEXICON,)
    )
    assert text == "urlencoded database123"
    assert applied == ()


def test_loanword_lexicon_untouched_words_stay_untouched() -> None:
    """office/check/GitHub already render correctly and must never be touched."""
    text, applied = apply_text_normalizations(
        "میں office میں check کر رہا ہوں GitHub پر۔",
        (TextNormalization.LOANWORD_LEXICON,),
    )
    assert text == "میں office میں check کر رہا ہوں GitHub پر۔"
    assert applied == ()


def test_both_normalizations_compose() -> None:
    text, applied = apply_text_normalizations(
        "ہمیں database کا 3 بار backup چاہیے۔",
        (TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON),
    )
    assert text == "ہمیں ڈیٹا بےس کا تین بار backup چاہیے۔"
    assert set(applied) == {TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON}


def test_no_normalizations_is_a_pure_pass_through() -> None:
    text, applied = apply_text_normalizations("کوئی بھی متن 123", ())
    assert text == "کوئی بھی متن 123"
    assert applied == ()


# ── The user-editable lexicon (2026-08-16) ──────────────────────────────────
#
# `apply_text_normalizations` now takes the lexicon as an argument so the
# per-user dictionary can be supplied without `routing.resolve()` doing I/O.
# These pin the behaviours the dictionary depends on; the shipped-default
# tests above pin that omitting the argument still behaves exactly as before.


def test_supplied_lexicon_replaces_the_shipped_defaults_entirely() -> None:
    """Not merged with the defaults — the caller decides the whole table."""
    text, applied = apply_text_normalizations(
        "ہمیں database چاہیے۔",
        (TextNormalization.LOANWORD_LEXICON,),
        lexicon={"chaiye": "چاہیے"},
    )
    assert text == "ہمیں database چاہیے۔", "the default `database` entry must not leak in"
    assert applied == ()


def test_lexicon_keys_may_be_perso_arabic() -> None:
    """
    A3's one defect: میٹنگ is read as "mating", and it arrives already in
    Perso-Arabic, so a Latin-keyed table can never match it.
    """
    text, applied = apply_text_normalizations(
        "کہ میٹنگ ملتوی ہو گئی ہے۔",
        (TextNormalization.LOANWORD_LEXICON,),
        lexicon={"میٹنگ": "مِیٹِنگ"},
    )
    assert text == "کہ مِیٹِنگ ملتوی ہو گئی ہے۔"
    assert applied == (TextNormalization.LOANWORD_LEXICON,)


def test_perso_arabic_keys_still_respect_word_boundaries() -> None:
    """`\b` is Unicode-aware, so a key must not match inside a longer word."""
    text, _ = apply_text_normalizations(
        "میٹنگوں میں",
        (TextNormalization.LOANWORD_LEXICON,),
        lexicon={"میٹنگ": "مِیٹِنگ"},
    )
    assert text == "میٹنگوں میں", "میٹنگ must not match inside میٹنگوں"


def test_lexicon_matching_is_case_insensitive() -> None:
    """A user writes the entry once; they will not type every case variant."""
    text, _ = apply_text_normalizations(
        "Database, DATABASE and database",
        (TextNormalization.LOANWORD_LEXICON,),
        lexicon={"database": "ڈیٹا بےس"},
    )
    assert text == "ڈیٹا بےس, ڈیٹا بےس and ڈیٹا بےس"


def test_longer_keys_win_over_shorter_ones() -> None:
    """
    Python alternation is first-match-wins and dict order is the user's, which
    is no order at all — so the pattern sorts by length itself.
    """
    text, _ = apply_text_normalizations(
        "open a pull request please",
        (TextNormalization.LOANWORD_LEXICON,),
        lexicon={"request": "رِیکویسٹ", "pull request": "پُل رِیکویسٹ"},
    )
    assert text == "open a پُل رِیکویسٹ please"


def test_empty_lexicon_is_a_pass_through_not_a_crash() -> None:
    """A user with no dictionary entries is the common case, not an edge one."""
    text, applied = apply_text_normalizations(
        "ہمیں database چاہیے۔", (TextNormalization.LOANWORD_LEXICON,), lexicon={}
    )
    assert text == "ہمیں database چاہیے۔"
    assert applied == ()


def test_shipped_defaults_include_the_perso_arabic_meeting_entry() -> None:
    """
    The first default keyed in Perso-Arabic — proving the either-script path is
    live in production, not merely supported by the code.
    """
    text, applied = apply_text_normalizations(
        "کل جب میں دفتر پہنچا تو پتہ چلا کہ میٹنگ ملتوی ہو گئی ہے۔",
        (TextNormalization.LOANWORD_LEXICON,),
    )
    assert text == "کل جب میں دفتر پہنچا تو پتہ چلا کہ مِیٹِنگ ملتوی ہو گئی ہے۔"
    assert applied == (TextNormalization.LOANWORD_LEXICON,)
