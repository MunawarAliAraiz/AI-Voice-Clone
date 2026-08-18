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
from app.domain.urdu_text import TextNormalization
from app.exceptions import AmbiguousScriptError, ModelNotFoundError, NoRouteError

URDU = "السلام علیکم، آپ کیسے ہیں؟"
DEVANAGARI_TEXT = "नमस्ते, आज का दिन कैसा है?"
ENGLISH = "Hello, how are you today?"
ROMAN_URDU = "Aap kaise hain, sab theek hai?"


# ── Stub catalog ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StubSpec:
    id: str
    display_name: str
    pairs: tuple[tuple[str, Script], ...]
    #: Pairs this stub CLAIMS but is not verified for — mirrors a real spec
    #: with experimental_listing=True and an unverified LanguageSupport cell.
    experimental_pairs: tuple[tuple[str, Script], ...] = ()
    #: Mirrors ModelSpec.text_normalizations — empty by default, same as a
    #: real spec that was never tested with any normalization.
    text_normalizations: tuple[TextNormalization, ...] = ()

    def supports(self, language: str, script: Script) -> bool:
        return (language, script) in self.pairs

    def supports_experimental(self, language: str, script: Script) -> bool:
        return (language, script) in self.experimental_pairs


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
# "hi"/Devanagari below is a STUB-ONLY language code — Hindi was fully removed
# as a real product language (no real spec claims it anymore). It survives
# here purely as a second, distinct (language, script) pair so multi-candidate
# routing mechanics (catalog-order preference, alternatives, the structural
# ROMAN_TO_DEVA/ARAB_TO_DEVA transform paths `routing.py` deliberately keeps
# in place) still have something concrete to exercise. Real routing.py never
# validates "hi" against `LanguageCode` — it is a plain string internally too.
SECOND_SPEC_A = StubSpec("chatterbox_ml_v3", "Chatterbox", (
    ("hi", Script.DEVANAGARI), ("en", Script.LATIN),
))
SECOND_SPEC_B = StubSpec("voxcpm2", "VoxCPM 2", (
    ("hi", Script.DEVANAGARI), ("hi", Script.LATIN),
    ("ur", Script.LATIN), ("en", Script.LATIN),
))
CATALOG = StubCatalog((URDU_SPEC, SECOND_SPEC_A, SECOND_SPEC_B))

#: Mirrors Chatterbox: claims (ur, ARABIC) but only experimentally — not in
#: `.pairs` (so `.supports()` is False), only in `.experimental_pairs`.
EXPERIMENTAL_SPEC = StubSpec(
    "experimental_model", "Experimental Model", pairs=(),
    experimental_pairs=(("ur", Script.ARABIC),),
)
CATALOG_WITH_EXPERIMENTAL = StubCatalog(
    (URDU_SPEC, SECOND_SPEC_A, SECOND_SPEC_B, EXPERIMENTAL_SPEC)
)

#: A second (ur, ARABIC) claimant, this one WITH normalizations declared —
#: mirrors OMNIVOICE_URDU vs. f5_openbible_urdu (URDU_SPEC, which declares
#: none). Both specs are reachable only via explicit `requested=`, since
#: auto-routing would always pick URDU_SPEC (catalog order).
NORMALIZING_SPEC = StubSpec(
    "normalizing_stub", "Normalizing Stub", (("ur", Script.ARABIC),),
    text_normalizations=(TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON),
)
CATALOG_WITH_NORMALIZING_SPEC = StubCatalog(
    (URDU_SPEC, NORMALIZING_SPEC, SECOND_SPEC_A, SECOND_SPEC_B)
)


# ── Script detection ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (URDU, Script.ARABIC),
        (DEVANAGARI_TEXT, Script.DEVANAGARI),
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


# ── Latin islands (code-switching) ───────────────────────────────────────────
#
# `detect_script` stays language-agnostic — `direction_analyze` relies on MIXED
# yielding the union of terminator sets. `profile_text` knows the declared
# language, so it is where an English island inside Urdu stops being "ambiguous".


CODE_SWITCHED_URDU = "میں نے GitHub پر ایک نیا پل ریکویسٹ بھیجا ہے"
HEAVY_CODE_SWITCH = "میں نے GitHub پر pull request create کر دی ہے"
# There used to be a third case here testing the same rescue for Hindi-declared
# Devanagari text. Removed along with Hindi: `NATIVE_SCRIPTS` (language.py) no
# longer has an entry for "hi", so the rescue this parametrize proves would no
# longer fire for it — Urdu is the only native-non-Latin-script language left
# to demonstrate this mechanism with.


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        # 82.9% Arabic — under the 0.85 dominance threshold, so raw detection
        # says MIXED. This is ordinary Urdu and must route.
        (CODE_SWITCHED_URDU, "ur", Script.ARABIC),
        # 63.9% Latin. Still Urdu: the Perso-Arabic carries the sentence frame.
        (HEAVY_CODE_SWITCH, "ur", Script.ARABIC),
    ],
)
def test_english_islands_do_not_make_native_text_ambiguous(
    text: str, language: str, expected: Script
) -> None:
    assert detect_script(text)[0] is Script.MIXED
    assert profile_text(text, language).script is expected


def test_island_rescue_keeps_the_true_measured_ratios() -> None:
    """Resolving the script must not hide that English was present."""
    profile = profile_text(CODE_SWITCHED_URDU, "ur")
    assert profile.script is Script.ARABIC
    assert profile.script_ratios[Script.LATIN] > 0.0
    assert profile.is_rtl


def test_two_native_scripts_stay_ambiguous() -> None:
    """
    Arabic-script Urdu mixed with substantial Devanagari is real ambiguity,
    not code-switching — the rescue only discounts LATIN islands (see
    `_resolve_latin_islands`'s docstring), never a second language-bearing
    script, regardless of whether that script belongs to a currently-declared
    product language.
    """
    assert profile_text("یہ اردو ہے मगर यह हिन्दी है", "ur").script is Script.MIXED


def test_a_token_of_native_script_does_not_hijack_english_text() -> None:
    """Past LATIN_ISLAND_CEILING the native script is no longer the frame."""
    mostly_english = "Hello everyone this is a mostly English sentence اردو"
    assert profile_text(mostly_english, "ur").script is not Script.ARABIC


def test_islands_never_apply_to_a_latin_native_language() -> None:
    """English has no native non-Latin script, so nothing is rescued for it."""
    assert profile_text("Hello नमस्ते dunya", "en").script is Script.MIXED


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


def test_roman_urdu_routes_directly_when_a_model_renders_it() -> None:
    # VoxCPM2 declares (ur, Latin), so Roman Urdu goes straight to it, untouched.
    plan = resolve(profile_text(ROMAN_URDU, "ur"), None, CATALOG)
    assert plan.model_id == "voxcpm2"
    assert plan.transform.kind is TransformKind.NONE
    assert not plan.needs_transform  # the worker gets the Roman text as-is
    assert not plan.lossy


def test_roman_urdu_falls_back_to_devanagari_hop_without_a_latin_model() -> None:
    """
    Catalog with no (ur, Latin) support: the lossless Roman->Devanagari one-hop
    is used so a Devanagari-only model could still serve it. No real catalog
    spec claims (hi, Devanagari) anymore (Hindi removed), so this fallback is
    currently always unreachable in production — this test proves the
    `routing.py` mechanism itself still works structurally, since it was
    deliberately left in place rather than deleted.
    """
    catalog = StubCatalog((URDU_SPEC, SECOND_SPEC_A))  # chatterbox has no (ur, Latin)
    plan = resolve(profile_text(ROMAN_URDU, "ur"), None, catalog)
    assert plan.model_id == "chatterbox_ml_v3"
    assert plan.transform.kind is TransformKind.ROMAN_TO_DEVA
    assert plan.needs_transform    # service layer must fill resolved_text
    assert "Roman Urdu" in plan.rationale


def test_perso_arabic_translit_strategy_is_lossy() -> None:
    """
    Same structural-coverage caveat as the fallback test above: in the real
    catalog this strategy is always `NoRouteError` now (no spec claims
    (hi, Devanagari)), but `_plan_transform`'s TRANSLITERATE branch is left in
    place, so it's still tested here against a stub catalog that does.
    """
    plan = resolve(profile_text(URDU, "ur"), None, CATALOG,
                   strategy=UrduStrategy.TRANSLITERATE)
    assert plan.transform.kind is TransformKind.ARAB_TO_DEVA
    assert plan.lossy, "short vowels are inferred — the UI must warn"


def test_second_language_and_english_route_directly() -> None:
    """Stub-catalog mechanics only — "hi" is not a real product language."""
    plan = resolve(profile_text(DEVANAGARI_TEXT, "hi"), None, CATALOG)
    assert plan.model_id == "chatterbox_ml_v3"
    assert resolve(profile_text(ENGLISH, "en"), None, CATALOG).model_id == "chatterbox_ml_v3"


def test_urdu_in_devanagari_is_rejected_with_a_suggestion() -> None:
    with pytest.raises(NoRouteError) as exc:
        resolve(profile_text(DEVANAGARI_TEXT, "ur"), None, CATALOG)
    assert "Devanagari" in exc.value.to_problem()["suggestion"]


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
    plan = resolve(profile_text(DEVANAGARI_TEXT, "hi"), "voxcpm2", CATALOG)
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
        resolve(profile_text(DEVANAGARI_TEXT, "hi"), "does_not_exist", CATALOG)


def test_experimental_model_refused_by_default() -> None:
    """allow_experimental defaults False — an unverified explicit pick is
    still refused, not silently allowed just because the spec claims it."""
    with pytest.raises(NoRouteError):
        resolve(profile_text(URDU, "ur"), "experimental_model", CATALOG_WITH_EXPERIMENTAL)


def test_experimental_model_honored_with_explicit_opt_in() -> None:
    plan = resolve(
        profile_text(URDU, "ur"), "experimental_model", CATALOG_WITH_EXPERIMENTAL,
        allow_experimental=True,
    )
    assert plan.model_id == "experimental_model"
    assert plan.experimental is True
    assert "EXPERIMENTAL" in plan.rationale


def test_experimental_opt_in_does_not_rescue_a_pair_not_even_claimed() -> None:
    """allow_experimental only widens supports() -> supports_experimental();
    it must not become a second, looser claims() check for ANY pair."""
    with pytest.raises(NoRouteError):
        resolve(
            profile_text(DEVANAGARI_TEXT, "hi"), "experimental_model", CATALOG_WITH_EXPERIMENTAL,
            allow_experimental=True,
        )


def test_experimental_opt_in_never_affects_auto_routing() -> None:
    """allow_experimental is only consulted for an explicit `requested` id.
    Auto (requested=None) must never pick an experimental-only spec."""
    plan = resolve(
        profile_text(URDU, "ur"), None, CATALOG_WITH_EXPERIMENTAL, allow_experimental=True,
    )
    assert plan.model_id == "f5_openbible_urdu"
    assert plan.experimental is False


def test_alternatives_exclude_the_chosen_model() -> None:
    plan = resolve(profile_text(DEVANAGARI_TEXT, "hi"), None, CATALOG)
    assert plan.model_id not in plan.alternatives
    assert "voxcpm2" in plan.alternatives


def test_text_normalization_applies_only_to_the_spec_that_declares_it() -> None:
    """
    The regression test that proves scoping is per-spec, not global: the SAME
    input, routed to two different specs claiming the SAME (language, script),
    is normalized only for the one that declares it.

    The carrier deliberately contains NO lexicon word, so the assertion below
    isolates `numbers`. It used to say میٹنگ, which became a shipped lexicon
    entry on 2026-08-16 and quietly turned this into a two-normalization test.
    """
    text_with_digits = "کلاس 3 بجے ہے"

    normalized = resolve(
        profile_text(text_with_digits, "ur"), "normalizing_stub",
        CATALOG_WITH_NORMALIZING_SPEC,
    )
    assert normalized.resolved_text != text_with_digits
    assert "تین" in normalized.resolved_text  # "3" expanded to "teen"
    assert normalized.text_normalizations == ("numbers",)

    unnormalized = resolve(
        profile_text(text_with_digits, "ur"), "f5_openbible_urdu",
        CATALOG_WITH_NORMALIZING_SPEC,
    )
    assert unnormalized.resolved_text == text_with_digits
    assert unnormalized.text_normalizations == ()


def test_text_normalization_reports_only_what_actually_changed() -> None:
    """
    A spec can declare LOANWORD_LEXICON and still see () reported for it when
    nothing in the text matched — the plan reflects reality, not capability.
    """
    plan = resolve(
        profile_text("عام سا جملہ ہے", "ur"), "normalizing_stub",
        CATALOG_WITH_NORMALIZING_SPEC,
    )
    assert plan.resolved_text == "عام سا جملہ ہے"
    assert plan.text_normalizations == ()


def test_normalization_is_mentioned_in_the_rationale() -> None:
    plan = resolve(
        profile_text("میٹنگ 3 بجے ہے", "ur"), "normalizing_stub",
        CATALOG_WITH_NORMALIZING_SPEC,
    )
    assert "numbers normalized" in plan.rationale


def test_resolve_is_pure() -> None:
    """Same inputs, same output. No clock, no randomness, no load state."""
    profile = profile_text(DEVANAGARI_TEXT, "hi")
    assert resolve(profile, None, CATALOG) == resolve(profile, None, CATALOG)


def test_resolve_ignores_load_state_entirely() -> None:
    """
    Routing must not prefer a model because it happens to be loaded. The stub
    catalog has no residency concept at all — which is the point: there is
    nowhere for load state to enter this function.
    """
    plan = resolve(profile_text(DEVANAGARI_TEXT, "hi"), None, CATALOG)
    assert plan.model_id == CATALOG.candidates("hi", Script.DEVANAGARI)[0].id


def test_with_resolved_text_closes_the_transform_seam() -> None:
    # Use a catalog with no (ur, Latin) model so the transform seam is exercised
    # (with a direct-Latin model the plan needs no transform at all).
    catalog = StubCatalog((URDU_SPEC, SECOND_SPEC_A))
    plan = resolve(profile_text(ROMAN_URDU, "ur"), None, catalog)
    assert plan.needs_transform
    final = plan.with_resolved_text("आप कैसे हैं")
    assert final.resolved_text == "आप कैसे हैं"
    assert final.model_id == plan.model_id
