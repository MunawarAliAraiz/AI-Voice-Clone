"""
Tests for the heuristic Speech-Direction analyzer (`app.domain.direction_analyze`).

Pure, no GPU, no I/O — mirrors the style of `tests/test_domain.py`: plain
module-level functions, no shared fixtures.
"""

from __future__ import annotations

from app.domain.direction import (
    DEFAULT_PAUSE_MS,
    DirectedSegment,
    DirectionPlan,
    Emotion,
    Level,
    Rate,
)
from app.domain.direction_analyze import analyze

#: Local-to-this-test ranking, purely so intensity/energy comparisons read as
#: `<`/`>` instead of repeating `Level.LOW`/`MEDIUM`/`HIGH` everywhere. The
#: analyzer itself has no notion of Level ordering.
_LEVEL_RANK = {Level.LOW: 0, Level.MEDIUM: 1, Level.HIGH: 2}


def _single_segment(text: str, language: str) -> DirectedSegment:
    plan = analyze(text, language)
    assert len(plan.segments) == 1, f"expected exactly one segment, got {plan.segments!r}"
    return plan.segments[0]


# ── Basic sanity across all three languages ─────────────────────────────────


def test_roman_urdu_produces_a_sensible_plan() -> None:
    plan = analyze("Aap kaise hain? Bohat khush hoon main!", "ur")
    assert isinstance(plan, DirectionPlan)
    assert plan.language == "ur"
    assert plan.source_script == "latin"
    assert len(plan.segments) == 2
    assert not plan.is_empty


def test_perso_arabic_urdu_produces_a_sensible_plan() -> None:
    plan = analyze("آپ کیسے ہیں؟ میں بہت خوش ہوں۔", "ur")
    assert plan.source_script == "arabic"
    assert len(plan.segments) == 2


def test_devanagari_hindi_produces_a_sensible_plan() -> None:
    plan = analyze("आप कैसे हैं? मुझे बहुत खुशी है।", "hi")
    assert plan.source_script == "devanagari"
    assert len(plan.segments) == 2


def test_english_produces_a_sensible_plan() -> None:
    plan = analyze("How are you? I am very happy today!", "en")
    assert plan.source_script == "latin"
    assert len(plan.segments) == 2


def test_segment_indices_are_sequential() -> None:
    plan = analyze("One. Two. Three.", "en")
    assert [s.index for s in plan.segments] == [0, 1, 2]


# ── Empty / whitespace ───────────────────────────────────────────────────────


def test_empty_input_yields_empty_plan_not_a_zero_length_segment() -> None:
    plan = analyze("", "en")
    assert plan.segments == ()
    assert plan.is_empty


def test_whitespace_only_input_yields_empty_plan() -> None:
    plan = analyze("   \n\t  ", "en")
    assert plan.segments == ()
    assert plan.is_empty


# ── Punctuation-driven emotion ───────────────────────────────────────────────


def test_question_mark_yields_questioning() -> None:
    seg = _single_segment("Are you coming today?", "en")
    assert seg.emotion is Emotion.QUESTIONING


def test_exclamation_yields_excited() -> None:
    seg = _single_segment("We won the match!", "en")
    assert seg.emotion is Emotion.EXCITED


def test_angry_keyword_beats_exclamation_punctuation() -> None:
    """"!" alone means EXCITED, but an angry-lexicon word wins regardless."""
    seg = _single_segment("I am so angry right now!", "en")
    assert seg.emotion is Emotion.ANGRY


def test_plain_statement_yields_neutral() -> None:
    seg = _single_segment("The train leaves at nine.", "en")
    assert seg.emotion is Emotion.NEUTRAL


def test_ellipsis_yields_calm() -> None:
    seg = _single_segment("I suppose we could wait...", "en")
    assert seg.emotion is Emotion.CALM


def test_roman_urdu_happy_keyword() -> None:
    seg = _single_segment("Main bohat khush hoon.", "ur")
    assert seg.emotion is Emotion.HAPPY


def test_perso_arabic_sad_keyword() -> None:
    seg = _single_segment("وہ بہت اداس ہے۔", "ur")
    assert seg.emotion is Emotion.SAD


def test_devanagari_sad_keyword() -> None:
    seg = _single_segment("वह बहुत उदास है।", "hi")
    assert seg.emotion is Emotion.SAD


def test_english_anxious_keyword() -> None:
    seg = _single_segment("I am so worried about this.", "en")
    assert seg.emotion is Emotion.ANXIOUS


def test_roman_urdu_anxious_keyword() -> None:
    seg = _single_segment("Mujhe bohat fikar hai.", "ur")
    assert seg.emotion is Emotion.ANXIOUS


def test_devanagari_anxious_keyword() -> None:
    seg = _single_segment("मुझे बहुत चिंतित हूं।", "hi")
    assert seg.emotion is Emotion.ANXIOUS


def test_anxious_keyword_beats_exclamation_punctuation() -> None:
    """"!" alone means EXCITED, but an anxious-lexicon word wins regardless —
    mirrors test_angry_keyword_beats_exclamation_punctuation, protecting
    ANXIOUS's placement ahead of EXCITED in _EMOTION_PRIORITY."""
    seg = _single_segment("I am so nervous right now!", "en")
    assert seg.emotion is Emotion.ANXIOUS


def test_sad_keyword_wins_over_anxious_keyword_in_same_segment() -> None:
    """Protects SAD's placement ahead of ANXIOUS in _EMOTION_PRIORITY: when
    both a sad-lexicon and an anxious-lexicon word appear, SAD wins regardless
    of word order in the text."""
    seg = _single_segment("Main bohat udaas hoon, aur thoda pareshan bhi.", "ur")
    assert seg.emotion is Emotion.SAD


# ── Intensity ────────────────────────────────────────────────────────────────


def test_intensity_rises_with_exclamation_caps_and_intensifier() -> None:
    plain = _single_segment("The train leaves at nine.", "en")
    loud = _single_segment("This is VERY exciting!!", "en")
    assert _LEVEL_RANK[loud.intensity] > _LEVEL_RANK[plain.intensity]


def test_hedge_word_lowers_intensity_below_plain() -> None:
    plain = _single_segment("The train leaves at nine.", "en")
    hedged = _single_segment("Maybe the train leaves at nine.", "en")
    assert _LEVEL_RANK[hedged.intensity] < _LEVEL_RANK[plain.intensity]


def test_energy_rises_with_excited_emotion() -> None:
    plain = _single_segment("The train leaves at nine.", "en")
    excited = _single_segment("We won the match!", "en")
    assert _LEVEL_RANK[excited.energy] > _LEVEL_RANK[plain.energy]


def test_energy_falls_with_calm_emotion() -> None:
    plain = _single_segment("The train leaves at nine.", "en")
    calm = _single_segment("Let's just stay calm about this.", "en")
    assert _LEVEL_RANK[calm.energy] <= _LEVEL_RANK[plain.energy]


# ── Rate ─────────────────────────────────────────────────────────────────────


def test_long_clause_dense_sentence_is_slow() -> None:
    seg = _single_segment(
        "First, we gather the ingredients, then, we mix them, "
        "and finally, carefully, patiently, we bake the whole thing.",
        "en",
    )
    assert seg.rate is Rate.SLOW


def test_short_exclamatory_sentence_is_fast() -> None:
    seg = _single_segment("Run now!", "en")
    assert seg.rate is Rate.FAST


def test_plain_medium_sentence_is_normal() -> None:
    seg = _single_segment("The train leaves at nine.", "en")
    assert seg.rate is Rate.NORMAL


# ── Emphasis ─────────────────────────────────────────────────────────────────


def test_allcaps_word_produces_emphasis_span() -> None:
    seg = _single_segment("Please STOP immediately.", "en")
    assert len(seg.emphasis) == 1
    span = seg.emphasis[0]
    assert seg.text[span.start : span.end] == "STOP"


def test_starred_word_produces_emphasis_span_excluding_asterisks() -> None:
    seg = _single_segment("This is *really* important.", "en")
    assert len(seg.emphasis) == 1
    span = seg.emphasis[0]
    assert seg.text[span.start : span.end] == "really"


def test_caps_and_asterisk_emphasis_combine_sorted_by_position() -> None:
    seg = _single_segment("NEVER say *never* again.", "en")
    assert len(seg.emphasis) == 2
    starts = [span.start for span in seg.emphasis]
    assert starts == sorted(starts)
    first, second = seg.emphasis
    assert seg.text[first.start : first.end] == "NEVER"
    assert seg.text[second.start : second.end] == "never"


def test_single_letter_caps_is_not_emphasized() -> None:
    """A lone capital "I" should not be mistaken for shouting."""
    seg = _single_segment("I am going home.", "en")
    assert seg.emphasis == ()


def test_no_emphasis_when_nothing_signals_it() -> None:
    seg = _single_segment("The train leaves at nine.", "en")
    assert seg.emphasis == ()


# ── pause_after_ms ───────────────────────────────────────────────────────────


def test_period_gets_base_pause() -> None:
    seg = _single_segment("The train leaves at nine.", "en")
    assert seg.pause_after_ms == DEFAULT_PAUSE_MS


def test_question_mark_gets_more_pause_than_period() -> None:
    seg = _single_segment("Are you coming today?", "en")
    assert seg.pause_after_ms > DEFAULT_PAUSE_MS


def test_ellipsis_gets_the_most_pause() -> None:
    period_seg = _single_segment("The train leaves at nine.", "en")
    question_seg = _single_segment("Are you coming today?", "en")
    ellipsis_seg = _single_segment("I suppose we could wait...", "en")
    assert ellipsis_seg.pause_after_ms > question_seg.pause_after_ms > period_seg.pause_after_ms


# ── Summary dominance ────────────────────────────────────────────────────────


def test_summary_reflects_dominant_emotion() -> None:
    text = "This is fine. I am so angry!! This makes me angry too!!"
    plan = analyze(text, "en")
    assert plan.summary.emotion is Emotion.ANGRY


def test_summary_ignores_neutral_when_any_other_emotion_present() -> None:
    text = "This is fine. That is also fine. I am so angry!!"
    plan = analyze(text, "en")
    assert plan.summary.emotion is Emotion.ANGRY


def test_summary_defaults_to_neutral_and_normal_when_nothing_signals() -> None:
    plan = analyze("The train leaves at nine. It arrives at ten.", "en")
    assert plan.summary.emotion is Emotion.NEUTRAL
    assert plan.summary.rate is Rate.NORMAL


def test_summary_is_derived_from_segments_not_independent() -> None:
    """Sanity check on the contract: summary must be consistent with segments."""
    plan = analyze("I am so angry!! This makes me angry too!!", "en")
    emotions = [s.emotion for s in plan.segments]
    assert plan.summary.emotion in emotions


# ── Determinism ──────────────────────────────────────────────────────────────


def test_analyze_is_deterministic() -> None:
    text = "Aap kaise hain? Bohat khush hoon main! Maybe thoda tired bhi."
    assert analyze(text, "ur") == analyze(text, "ur")


def test_analyze_is_deterministic_across_languages() -> None:
    for text, language in (
        ("आप कैसे हैं? मुझे बहुत खुशी है।", "hi"),
        ("آپ کیسے ہیں؟ میں بہت خوش ہوں۔", "ur"),
        ("How are you? I am very happy today!", "en"),
    ):
        assert analyze(text, language) == analyze(text, language)
