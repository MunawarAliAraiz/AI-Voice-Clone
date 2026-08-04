"""
Domain layer tests — pure, no GPU, no torch, microseconds.

Note the stub catalog: `resolve()` is tested against five lines of fake specs
rather than the real one. That is the payoff of `domain.ports.CatalogView` —
routing logic is exercised with no ModelSpec, no licenses, and no HuggingFace
ids anywhere near it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.language import Script, detect_script, profile_text
from app.domain.routing import TransformKind, UrduStrategy, resolve
from app.domain.text import chunk_for_synthesis, normalize_whitespace, split_sentences
from app.exceptions import AmbiguousScriptError, ModelNotFoundError, NoRouteError

URDU = "السلام علیکم، آپ کیسے ہیں؟"
HINDI = "नमस्ते, आज का दिन कैसा है?"
ENGLISH = "Hello, how are you today?"
ROMAN_URDU = "Aap kaise hain, sab theek hai?"


# ── Stub catalog ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StubSpec:
    id: str
    display_name: str
    pairs: tuple[tuple[str, Script], ...]

    def supports(self, language: str, script: Script) -> bool:
        return (language, script) in self.pairs


@dataclass(frozen=True)
class StubCatalog:
    specs: tuple[StubSpec, ...]

    def candidates(self, language: str, script: Script) -> tuple[StubSpec, ...]:
        return tuple(s for s in self.specs if s.supports(language, script))

    def get(self, model_id: str) -> StubSpec | None:
        return next((s for s in self.specs if s.id == model_id), None)

    def supported_pairs(self) -> tuple[tuple[str, Script], ...]:
        seen: list[tuple[str, Script]] = []
        for spec in self.specs:
            for pair in spec.pairs:
                if pair not in seen:
                    seen.append(pair)
        return tuple(seen)


URDU_SPEC = StubSpec("f5_openbible_urdu", "OpenBible Urdu", (("ur", Script.ARABIC),))
HINDI_A = StubSpec("chatterbox_ml_v3", "Chatterbox", (
    ("hi", Script.DEVANAGARI), ("en", Script.LATIN),
))
HINDI_B = StubSpec("voxcpm2", "VoxCPM 2", (("hi", Script.DEVANAGARI),))
CATALOG = StubCatalog((URDU_SPEC, HINDI_A, HINDI_B))


# ── Script detection ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (URDU, Script.ARABIC),
        (HINDI, Script.DEVANAGARI),
        (ENGLISH, Script.LATIN),
        (ROMAN_URDU, Script.LATIN),
        ("", Script.UNKNOWN),
        ("123 456 !!! ???", Script.UNKNOWN),
    ],
)
def test_detect_script(text: str, expected: Script) -> None:
    assert detect_script(text)[0] is expected


def test_digits_and_punctuation_do_not_make_text_mixed() -> None:
    """"Hello! 123" is Latin, not MIXED. Only scripted characters count."""
    assert detect_script("Hello! 123 -- (yes).")[0] is Script.LATIN


def test_genuinely_mixed_script_is_flagged() -> None:
    script, ratios = detect_script("Hello नमस्ते dunya")
    assert script is Script.MIXED
    assert set(ratios) == {Script.LATIN, Script.DEVANAGARI}


def test_roman_urdu_is_indistinguishable_from_english_by_script() -> None:
    """
    The reason there is no classifier: both are Latin. The user's declared
    language is the ONLY thing that separates them.
    """
    assert detect_script(ROMAN_URDU)[0] is detect_script(ENGLISH)[0] is Script.LATIN

    roman = profile_text(ROMAN_URDU, "ur")
    english = profile_text(ENGLISH, "en")
    assert roman.is_roman_urdu
    assert not english.is_roman_urdu


def test_rtl_keys_off_script_not_language() -> None:
    """`dir="rtl"` off `language == 'ur'` wrongly right-aligns Roman Urdu."""
    assert profile_text(URDU, "ur").is_rtl
    assert not profile_text(ROMAN_URDU, "ur").is_rtl


# ── Sentence splitting ───────────────────────────────────────────────────────


def test_normalize_whitespace() -> None:
    assert normalize_whitespace("  a \n\n b\tc  ") == "a b c"


def test_split_urdu_full_stop() -> None:
    """`۔` U+06D4 — a splitter that only knows `.` returns one giant chunk."""
    out = split_sentences("پہلا جملہ۔ دوسرا جملہ۔ تیسرا؟", Script.ARABIC)
    assert len(out) == 3


def test_split_devanagari_danda() -> None:
    assert len(split_sentences("पहला वाक्य। दूसरा वाक्य। तीसरा?", Script.DEVANAGARI)) == 3


def test_split_latin() -> None:
    assert split_sentences("One. Two! Three?", Script.LATIN) == ["One.", "Two!", "Three?"]


def test_terminator_run_stays_one_sentence() -> None:
    assert split_sentences("What?! Really.", Script.LATIN) == ["What?!", "Really."]


def test_decimals_and_initials_do_not_split() -> None:
    assert split_sentences("Pi is 3.14 exactly.", Script.LATIN) == ["Pi is 3.14 exactly."]
    assert len(split_sentences("J. Smith arrived.", Script.LATIN)) == 1


def test_empty_input_yields_no_sentences() -> None:
    """`[""]` would become a zero-length synthesis request."""
    assert split_sentences("   ", Script.LATIN) == []
    assert chunk_for_synthesis("", Script.LATIN, max_chars=100) == []


# ── Chunking ─────────────────────────────────────────────────────────────────


def test_chunks_respect_max_chars() -> None:
    text = " ".join(f"Sentence number {i} here." for i in range(20))
    chunks = chunk_for_synthesis(text, Script.LATIN, max_chars=80)
    assert chunks
    assert all(len(c.text) <= 80 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_oversized_sentence_splits_and_is_flagged() -> None:
    """A chunk not ending on sentence punctuation is where prosody artifacts land."""
    long_sentence = "word " * 100 + "end."
    chunks = chunk_for_synthesis(long_sentence, Script.LATIN, max_chars=60)
    assert len(chunks) > 1
    assert any(not c.ends_on_sentence for c in chunks)
    assert all(len(c.text) <= 60 for c in chunks)


def test_runt_trailing_chunk_is_merged_back() -> None:
    """A two-word final chunk renders with audibly clipped prosody."""
    chunks = chunk_for_synthesis(
        "This is a reasonably long first sentence. Ok.",
        Script.LATIN, max_chars=60, min_chars=10,
    )
    assert all(len(c.text) >= 10 for c in chunks)


def test_max_chars_must_be_positive() -> None:
    with pytest.raises(ValueError):
        chunk_for_synthesis("hi", Script.LATIN, max_chars=0)


# ── Routing ──────────────────────────────────────────────────────────────────


def test_native_urdu_routes_to_urdu_spec_untransformed() -> None:
    plan = resolve(profile_text(URDU, "ur"), None, CATALOG)
    assert plan.model_id == "f5_openbible_urdu"
    assert plan.transform.kind is TransformKind.NONE
    assert not plan.lossy
    assert not plan.needs_transform


def test_roman_urdu_routes_to_hindi_via_one_hop() -> None:
    plan = resolve(profile_text(ROMAN_URDU, "ur"), None, CATALOG)
    assert plan.model_id == "chatterbox_ml_v3"
    assert plan.transform.kind is TransformKind.ROMAN_TO_DEVA
    assert not plan.lossy          # one hop, script-only
    assert plan.needs_transform    # service layer must fill resolved_text
    assert "Roman Urdu" in plan.rationale


def test_perso_arabic_translit_strategy_is_lossy() -> None:
    plan = resolve(profile_text(URDU, "ur"), None, CATALOG,
                   strategy=UrduStrategy.TRANSLITERATE)
    assert plan.transform.kind is TransformKind.ARAB_TO_DEVA
    assert plan.lossy, "short vowels are inferred — the UI must warn"


def test_hindi_and_english_route_directly() -> None:
    assert resolve(profile_text(HINDI, "hi"), None, CATALOG).model_id == "chatterbox_ml_v3"
    assert resolve(profile_text(ENGLISH, "en"), None, CATALOG).model_id == "chatterbox_ml_v3"


def test_urdu_in_devanagari_is_rejected_with_a_suggestion() -> None:
    with pytest.raises(NoRouteError) as exc:
        resolve(profile_text(HINDI, "ur"), None, CATALOG)
    assert "hi" in exc.value.to_problem()["suggestion"]


def test_mixed_script_is_rejected_rather_than_guessed() -> None:
    with pytest.raises(AmbiguousScriptError):
        resolve(profile_text("Hello नमस्ते dunya", "en"), None, CATALOG)


def test_unroutable_enumerates_what_would_work() -> None:
    """No silent fallback. The 422 must be actionable."""
    with pytest.raises(NoRouteError) as exc:
        resolve(profile_text("Bonjour tout le monde", "fr"), None, CATALOG)
    problem = exc.value.to_problem()
    assert problem["status"] == 422
    assert problem["supported"], "must list what WOULD work"


def test_explicit_model_is_honored() -> None:
    plan = resolve(profile_text(HINDI, "hi"), "voxcpm2", CATALOG)
    assert plan.model_id == "voxcpm2"


def test_explicit_model_that_cannot_serve_is_refused_not_swapped() -> None:
    """
    THE regression that must never return: an explicit request is refused, never
    silently replaced with whatever else could run.
    """
    with pytest.raises(NoRouteError):
        resolve(profile_text(URDU, "ur"), "voxcpm2", CATALOG)


def test_unknown_model_id_raises() -> None:
    with pytest.raises(ModelNotFoundError):
        resolve(profile_text(HINDI, "hi"), "does_not_exist", CATALOG)


def test_alternatives_exclude_the_chosen_model() -> None:
    plan = resolve(profile_text(HINDI, "hi"), None, CATALOG)
    assert plan.model_id not in plan.alternatives
    assert "voxcpm2" in plan.alternatives


def test_resolve_is_pure() -> None:
    """Same inputs, same output. No clock, no randomness, no load state."""
    profile = profile_text(HINDI, "hi")
    assert resolve(profile, None, CATALOG) == resolve(profile, None, CATALOG)


def test_resolve_ignores_load_state_entirely() -> None:
    """
    Routing must not prefer a model because it happens to be loaded. The stub
    catalog has no residency concept at all — which is the point: there is
    nowhere for load state to enter this function.
    """
    plan = resolve(profile_text(HINDI, "hi"), None, CATALOG)
    assert plan.model_id == CATALOG.candidates("hi", Script.DEVANAGARI)[0].id


def test_with_resolved_text_closes_the_transform_seam() -> None:
    plan = resolve(profile_text(ROMAN_URDU, "ur"), None, CATALOG)
    assert plan.needs_transform
    final = plan.with_resolved_text("आप कैसे हैं")
    assert final.resolved_text == "आप कैसे हैं"
    assert final.model_id == plan.model_id
