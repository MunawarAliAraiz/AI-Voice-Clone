"""
The validator that stands between an editable instruction and the text you
generate.

Pure — no model, no GPU, no scheduler. That is the point of putting it in
`domain/`: the rule the user cannot switch off is the one thing here that must
be provable without a 19 GB model.
"""

from __future__ import annotations

import pytest

from app.domain.transliterate import (
    TransliterationRejected,
    validate_transliteration,
)

#: The A3 corpus's opening line, and its gold conversion.
ROMAN = "Assalam o alaikum, kya haal hai aap ka?"
URDU = "السلام علیکم، کیا حال ہے آپ کا؟"


def test_a_real_conversion_passes() -> None:
    check = validate_transliteration(ROMAN, URDU)
    assert check.arabic_share > 0.9
    assert 0.4 < check.length_ratio < 2.5


def test_code_switched_output_passes() -> None:
    """
    The contract says to KEEP English words in Latin, so a correct conversion
    is routinely mixed-script. A threshold tuned for "all Urdu" would reject
    the very output the prompt asks for — and this corpus is 57%
    code-switched.
    """
    check = validate_transliteration(
        "Kal office mein meeting hai aur pull request bhi bhejna hai.",
        "کل office میں meeting ہے اور pull request بھی بھیجنا ہے۔",
    )
    # 0.467 measured. An intuition-picked 0.5 floor rejected this exact
    # string, which is why the threshold is set from measurement instead.
    assert check.arabic_share < 0.5


def test_a_mostly_latin_conversion_still_passes() -> None:
    """
    The case that sets the floor. This is a CORRECT conversion — the contract
    says keep English in Latin — and it measures 0.115 Perso-Arabic. Every
    genuine failure measures 0.000, so the threshold has to sit in that gap
    and nowhere near "mostly Urdu".
    """
    check = validate_transliteration(
        "Meeting ke baad pull request review kar lena, deadline Friday hai.",
        "Meeting کے baad pull request review کر lena، deadline Friday ہے۔",
    )
    assert check.arabic_share < 0.2


def test_an_echo_of_the_input_is_rejected_and_says_so() -> None:
    """The instruction is editable, so "the model ignored it" is a real
    outcome and the message has to name it rather than say "invalid"."""
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(ROMAN, ROMAN)
    assert exc.value.reason == "not_urdu_script"
    assert "echoed" in exc.value.detail


def test_an_english_answer_is_rejected() -> None:
    """The failure mode an editable instruction introduces: a model that
    ANSWERS the text instead of converting it."""
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(ROMAN, "Hello! I am doing very well, thank you for asking.")
    assert exc.value.reason == "not_urdu_script"


def test_a_refusal_is_rejected() -> None:
    with pytest.raises(TransliterationRejected):
        validate_transliteration(ROMAN, "I'm sorry, I can't help with that request.")


def test_empty_output_is_rejected_as_empty_not_as_wrong_script() -> None:
    """Distinct reason codes because the user's next move differs: retry a
    blank, rewrite the instruction for a wrong-script reply."""
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(ROMAN, "   \n ")
    assert exc.value.reason == "empty"


def test_a_summary_in_urdu_is_rejected_on_length() -> None:
    """Right script, wrong job. Script alone cannot catch this one."""
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(
            "Assalam o alaikum, kya haal hai aap ka? Bohat dinon baad baat hui. "
            "Kal office mein meeting hai aur mujhe report bhi taiyar karni hai.",
            "سلام۔",
        )
    assert exc.value.reason == "too_short"


def test_urdu_commentary_is_rejected_on_length() -> None:
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration("Kal meeting hai.", "کل میٹنگ ہے۔ " * 12)
    assert exc.value.reason == "too_long"


def test_perso_arabic_being_shorter_than_roman_is_not_a_failure() -> None:
    """
    Perso-Arabic drops short vowels, so a correct conversion is routinely
    shorter than its Roman source. The lower bound has to sit below that or
    it rejects correct work — and a false rejection costs a ~19 GB reload.
    """
    check = validate_transliteration("aap kaise hain", "آپ کیسے ہیں")
    assert check.length_ratio < 1.0


def test_the_validator_does_not_claim_the_conversion_is_correct() -> None:
    """
    The guard against this being mistaken for the gate. Run 2's real defects
    were valid Urdu words meaning something ELSE — کال (call) for کل
    (tomorrow) — and they pass here, exactly as they must. Numeric screens can
    only fail a candidate, never approve one; the human reading the editable
    output is the judge.
    """
    check = validate_transliteration("Kal meeting hai", "کال میٹنگ ہے")
    assert check.arabic_share > 0.9
