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


def test_loanword_lexicon_respells_database_leaving_base_latin() -> None:
    # docs/URDU_BAKEOFF_RESULTS.md SS5d: the all-Urdu "ڈیٹا بیس" was rejected
    # (بیس collides with the Urdu word for "twenty"). The mixed form keeps
    # "base" in Latin, matching office/check/GitHub which already work as-is.
    text, applied = apply_text_normalizations(
        "ہمیں database چاہیے۔", (TextNormalization.LOANWORD_LEXICON,)
    )
    assert text == "ہمیں ڈیٹا base چاہیے۔"
    assert applied == (TextNormalization.LOANWORD_LEXICON,)


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
    assert text == "ہمیں ڈیٹا base کا تین بار backup چاہیے۔"
    assert set(applied) == {TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON}


def test_no_normalizations_is_a_pure_pass_through() -> None:
    text, applied = apply_text_normalizations("کوئی بھی متن 123", ())
    assert text == "کوئی بھی متن 123"
    assert applied == ()
