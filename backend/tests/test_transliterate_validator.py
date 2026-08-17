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
    TARGET_ROMAN,
    TransliterationRejected,
    source_script_of,
    target_script_for,
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


# --- source-script selection ------------------------------------------------
# Which exemplar set the prompt gets. Pure, so it is settled here rather than
# on a GPU.


def test_roman_urdu_selects_the_latin_exemplars() -> None:
    assert source_script_of("Kal office mein meeting hai") == "latin"


def test_perso_arabic_is_its_own_source_not_lumped_into_latin() -> None:
    """It used to fall into `latin`, on the reasoning that text already in the
    target has no business being converted. It does now: Urdu script -> Roman
    is how an Urdu-script caption becomes editable."""
    assert source_script_of("کل office میں meeting ہے") == "arabic"
    assert target_script_for("arabic") == TARGET_ROMAN


def test_plain_hindi_selects_the_devanagari_exemplars() -> None:
    assert source_script_of("मुझे समझ नहीं आ रहा कि ये कैसे हुआ।") == "devanagari"


def test_a_code_switched_hindi_caption_is_still_devanagari() -> None:
    """
    THE CASE `detect_script` GETS WRONG FOR THIS PURPOSE. A Hindi caption
    carrying English words can fail the dominance threshold and come back
    MIXED — which would fall back to Latin and show the model six `Roman:`
    examples for text it cannot read as Roman. Presence decides here, not
    dominance.
    """
    text = "Client के साथ meeting reschedule हो गई है, अब Friday को है।"
    assert source_script_of(text) == "devanagari"


def test_one_stray_devanagari_character_does_not_swing_roman_urdu() -> None:
    """The floor's whole job. Otherwise a single quoted glyph rewrites the
    entire prompt for a sentence that is plainly Roman Urdu."""
    text = "Mujhe nahi pata ke क ka matlab kya hai in this whole long sentence"
    assert source_script_of(text) == "latin"


def test_text_with_no_letters_falls_back_to_latin() -> None:
    assert source_script_of("123 — !!") == "latin"


# --- the echo check, both scripts -------------------------------------------


def test_a_devanagari_echo_is_reported_as_an_echo_not_a_wrong_script() -> None:
    """
    The bug this replaced: the echo branch required Script.LATIN, so a model
    that handed a Hindi caption straight back was told it "replied in the
    wrong script". True, and useless — it sends the user to fix the
    instruction's wording when the instruction was ignored outright.
    """
    hindi = "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।"
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(hindi, hindi)
    assert exc.value.reason == "not_urdu_script"
    assert "echoed" in exc.value.detail


def test_a_latin_echo_still_reports_as_an_echo() -> None:
    roman = "Kal office mein meeting hai"
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(roman, roman)
    assert "echoed" in exc.value.detail


def test_different_wrong_script_output_is_not_called_an_echo() -> None:
    """An answer in the wrong script is a different failure from a refusal to
    act, and the message has to keep them apart."""
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration("Kal meeting hai", "Tomorrow there is a meeting.")
    assert "echoed" not in exc.value.detail


# --- the Roman target ---------------------------------------------------------
# A different question entirely: not "is the target script present" (Roman Urdu
# and English are both Latin, so that is unanswerable) but "is the SOURCE script
# gone".


def test_a_correct_devanagari_to_roman_conversion_passes() -> None:
    check = validate_transliteration(
        "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।",
        "mujhe samajh nahi aa raha ke ye kaise hua.",
        TARGET_ROMAN,
    )
    assert check.residual_source_share == 0.0


def test_the_same_output_would_be_REJECTED_for_a_perso_arabic_target() -> None:
    """
    The reason the validator had to become target-aware at all. Roman output
    contains no Perso-Arabic, so the gated hop's own check fires on every
    correct conversion of the transcript hop.
    """
    with pytest.raises(TransliterationRejected):
        validate_transliteration(
            "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।",
            "mujhe samajh nahi aa raha ke ye kaise hua.",
        )


def test_devanagari_left_in_the_output_is_rejected() -> None:
    hindi = "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।"
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(hindi, hindi, TARGET_ROMAN)
    assert exc.value.reason == "not_converted"
    assert "echoed" in exc.value.detail


def test_urdu_script_left_in_the_output_is_rejected() -> None:
    urdu = "کل میٹنگ ہے اور مجھے report بھی تیار کرنی ہے۔"
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(urdu, urdu, TARGET_ROMAN)
    assert exc.value.reason == "not_converted"


def test_a_partly_converted_line_still_fails() -> None:
    """Half-done is not done. The check is on RESIDUE, so leaving a clause
    behind fails even though most of the sentence converted."""
    with pytest.raises(TransliterationRejected):
        validate_transliteration(
            "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।",
            "mujhe samajh nahi aa raha कि ये कैसे हुआ।",
            TARGET_ROMAN,
        )


def test_an_english_translation_PASSES_and_that_is_the_known_hole() -> None:
    """
    NOT a bug to fix — a limitation to know. Roman Urdu and English are both
    Latin, so no threshold can separate "converted" from "translated". This
    test exists so nobody later reads the passing check as proof the output is
    Roman Urdu. What covers it is a human reading the draft, which is the
    entire reason a Roman target exists.
    """
    check = validate_transliteration(
        "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।",
        "I do not understand how this happened at all.",
        TARGET_ROMAN,
    )
    assert check.residual_source_share == 0.0


def test_a_roman_answer_is_still_caught_by_length() -> None:
    """The one screen that does survive into this direction."""
    with pytest.raises(TransliterationRejected) as exc:
        validate_transliteration(
            "मुझे समझ नहीं आ रहा।", "acha " * 60, TARGET_ROMAN
        )
    assert exc.value.reason == "too_long"
