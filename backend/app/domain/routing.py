"""
AI Voice Clone Studio — Language routing.

CONTRACT MODULE. Wave 0. `resolve()` is B3's to implement.

`resolve` is a PURE FUNCTION. No I/O, no clock, no randomness, and — the part
that matters — no knowledge of what is currently loaded.

Routing that consults load state is nondeterministic by construction, and it is
the exact root cause of the defect this rewrite exists to kill: the predecessor
picked "the first engine with is_loaded=True", which on a cold server was only
ever the mock, so every default request returned a 440Hz sine wave with HTTP 200.

    Routing decides what SHOULD run. The scheduler makes it so.

Keep those two jobs in separate modules and that bug cannot be written again.

NO SILENT FALLBACK. Ever. An unroutable request raises `NoRouteError`, which
becomes a 422 whose body enumerates exactly what WOULD have worked. A user must
never be left wondering why their Urdu came out sounding like Hindi.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from ..exceptions import AmbiguousScriptError, ModelNotFoundError, NoRouteError
from .language import LanguageCode, Script, TextProfile
from .ports import CatalogView, SpecView
from .urdu_text import DEFAULT_LOANWORD_LEXICON, apply_text_normalizations

__all__ = [
    "TransformKind",
    "UrduStrategy",
    "TextTransform",
    "RoutePlan",
    "resolve",
]


class TransformKind(StrEnum):
    """A text transformation applied before synthesis."""

    #: Text reaches the model unchanged. The only lossless option.
    NONE = "none"
    #: Roman Urdu -> Devanagari. One hop, via ai4bharat-transliteration (MIT).
    ROMAN_TO_DEVA = "roman_to_deva"
    #: Perso-Arabic -> Roman -> Devanagari. Two hops, LOSSY: Perso-Arabic omits
    #: short vowels, so the intermediate romanization guesses them. Only ever
    #: used when the user explicitly opts in via UrduStrategy.TRANSLITERATE.
    ARAB_TO_DEVA = "arab_to_deva"


class UrduStrategy(StrEnum):
    """
    How to handle Urdu, when there is a choice.

    Originally existed because no truly-free model does Urdu zero-shot
    cloning *well*, and routing Perso-Arabic Urdu through a stronger Hindi
    model via transliteration was a candidate escape hatch. Hindi has since
    been fully removed as a target language (see CLAUDE.md), so
    `TRANSLITERATE` below is now permanently unroutable — kept as a
    structurally valid enum value (not deleted) because it was already
    unimplemented before the removal (see `TRANSLITERATE`'s own docstring);
    a request that sets it correctly gets `NoRouteError`, not a crash.
    """

    #: Perso-Arabic goes to the native Urdu checkpoint. The default, and the
    #: only strategy with a live route.
    NATIVE = "native"
    #: Was meant to transliterate Perso-Arabic to Devanagari for a Hindi
    #: model. Never actually implemented (`ARAB_TO_DEVA` has no real
    #: transliteration call behind it), and now permanently unroutable since
    #: Hindi was removed — always `NoRouteError`. Left in place rather than
    #: deleted; see `resolve()`'s routing-table docstring.
    TRANSLITERATE = "translit"


@dataclass(frozen=True, slots=True)
class TextTransform:
    """One transformation step, with its cost made explicit."""

    kind: TransformKind
    source_script: Script
    target_script: Script
    #: True if the transform can lose information that changes pronunciation.
    lossy: bool = False

    @property
    def is_identity(self) -> bool:
        return self.kind is TransformKind.NONE


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """
    The decision. Returned by `resolve`, consumed by the service layer, and
    echoed to the client in every response as a visible chip.

    `rationale` is user-facing prose, not a debug string. "Roman Urdu
    transliterated to Devanagari and rendered by IndicF5" is the standard the
    text must meet — it is the difference between a user trusting the output and
    filing a bug about the wrong accent.
    """

    model_id: str
    transform: TextTransform
    #: The exact string the model will receive, post-transform. The service
    #: layer passes THIS to SynthRequest.text; it does not re-derive it.
    resolved_text: str
    #: The language the user declared. Carried through for history and display.
    requested_language: str
    #: Script measured on the original input.
    source_script: Script
    rationale: str
    #: Specs that would also have served this request, in preference order.
    #: Surfaced in the UI as "or try…" and used by the scheduler as fallback
    #: candidates ONLY on hard failure (worker crash), never on cold-start —
    #: preferring a resident model over the right one is the original sin here.
    alternatives: tuple[str, ...] = ()
    #: True when this route only exists because the caller explicitly named
    #: an unverified model AND set `allow_experimental=True` — never true for
    #: Auto routing. The UI must render this distinctly (rule 5's spirit:
    #: routing to a model that failed its own accuracy gate must never look
    #: like routing to a verified one).
    experimental: bool = False
    #: Which of the chosen spec's declared `text_normalizations` were actually
    #: applied to `resolved_text`. Reflects reality, not capability — a spec
    #: can declare `LOANWORD_LEXICON` and still report `()` here if nothing in
    #: this particular text matched. Empty for every spec that declares none
    #: (see `ModelSpec.text_normalizations`) — never applied globally.
    text_normalizations: tuple[str, ...] = ()

    @property
    def lossy(self) -> bool:
        """True if the transform may have altered pronunciation."""
        return self.transform.lossy

    @property
    def needs_transform(self) -> bool:
        """
        True if `resolved_text` is still the ORIGINAL text and a transform must
        be applied before synthesis.

        This is the seam between the pure and impure halves. `resolve()` cannot
        transliterate — that needs a model, which is I/O — so it returns the
        plan with `resolved_text` set to the input. The service layer applies
        the transform and calls `with_resolved_text()`. A worker must never
        receive a plan where this is still True.
        """
        return not self.transform.is_identity

    def with_resolved_text(self, text: str) -> RoutePlan:
        """Return a copy carrying the post-transform text."""
        return replace(self, resolved_text=text)


def resolve(
    profile: TextProfile,
    requested: str | None,
    catalog: CatalogView,
    strategy: UrduStrategy = UrduStrategy.NATIVE,
    allow_experimental: bool = False,
    lexicon: Mapping[str, str] = DEFAULT_LOANWORD_LEXICON,
) -> RoutePlan:
    """
    Decide which spec should render `profile`, and how the text must be shaped.

    Pure. Same inputs, same output, forever. Not async, because it performs no
    I/O — if an implementation ever needs to await something, the design has
    been broken rather than extended.

    Args:
        profile: user-declared language plus measured script.
        requested: an explicit model id, or None to let the catalog decide.
            An explicit id is honored if it verifiably supports the pair, and
            raises otherwise. It is NEVER silently swapped for something else.
        catalog: anything satisfying `CatalogView`. Typed as a protocol rather
            than `ModelCatalog` so the domain layer never imports the inference
            layer — the dependency arrow runs inference -> domain only.
        strategy: how to treat Perso-Arabic Urdu.
        allow_experimental: only meaningful together with `requested`. If the
            requested spec doesn't verifiably support the pair but has opted
            into experimental listing (`ModelSpec.experimental_listing`) and
            claims the pair anyway, honor it with `RoutePlan.experimental=True`
            instead of raising. Never consulted for Auto routing.

    Returns:
        A RoutePlan naming exactly one spec.

    Raises:
        NoRouteError: nothing in the catalog serves this (language, script),
            or `requested` cannot. The error carries `catalog.supported_pairs()`
            so the 422 body can tell the user what would work.
        AmbiguousScriptError: the text mixes scripts substantially. Guessing
            here produces audibly broken output; asking is better.

    The routing table:

        ur + Perso-Arabic                 -> f5_openbible_urdu, transform NONE
        ur + Perso-Arabic (TRANSLITERATE) -> NoRouteError (see below)
        ur + Latin  (Roman Urdu)          -> voxcpm2, transform NONE
        en + Latin                        -> best English spec, transform NONE
        anything else                     -> NoRouteError

    Hindi was fully removed as a target language (see CLAUDE.md) — its
    catalog `LanguageSupport` cells are gone, so `hi`/Devanagari is no longer
    a routable destination for anything. `UrduStrategy.TRANSLITERATE` and the
    Roman-Urdu Devanagari fallback both used to target a Hindi spec and are
    now unreachable in practice (they were already dead code before this:
    TRANSLITERATE's transform was never actually implemented, and the Roman
    fallback never fired because `voxcpm2` already serves `(ur, Latin)`
    natively). Both are left in place structurally rather than deleted — they
    correctly degrade to `NoRouteError` (empty candidates for `hi`) instead
    of being surgically removed from this contract module.

    (ur, DEVANAGARI) is rejected on purpose rather than accommodated — no
    spec renders Urdu from Devanagari, and accepting it would quietly make
    the language field meaningless. Likewise (en, ARABIC) and friends — no
    spec claims them.

    And note what is NOT here: no Roman-Urdu-versus-English classifier. Both are
    Latin, short inputs are genuinely ambiguous, and a classifier would be
    confidently wrong forever. The user's declared language settles it.
    """
    if profile.script is Script.MIXED:
        raise AmbiguousScriptError(
            {s.value: r for s, r in profile.script_ratios.items()}
        )

    target, transform = _plan_transform(profile, strategy, catalog)
    candidates = catalog.candidates(target.language, target.script)

    experimental = False
    if requested is not None:
        spec = catalog.get(requested)
        if spec is None:
            raise ModelNotFoundError(requested)
        # An explicit request is honored or refused. It is NEVER silently
        # swapped — that substitution is the whole defect being designed out.
        if not spec.supports(target.language, target.script):
            if allow_experimental and spec.supports_experimental(
                target.language, target.script
            ):
                experimental = True
            else:
                raise NoRouteError(
                    language=profile.language,
                    script=profile.script.value,
                    supported=_supported(catalog),
                    suggestion=(
                        f"Model {requested!r} cannot render {profile.language!r} in "
                        f"{profile.script.value} script."
                    ),
                )
        chosen: SpecView = spec
    else:
        if not candidates:
            raise NoRouteError(
                language=profile.language,
                script=profile.script.value,
                supported=_supported(catalog),
                suggestion=_suggest(profile),
            )
        # Catalog order IS preference order, and it consults no load state.
        chosen = candidates[0]

    # Pronunciation normalization is pure (table lookup + regex), unlike
    # TransformKind's script transforms which need a model — so it runs here,
    # synchronously, rather than through the impure with_resolved_text() seam.
    #
    # `lexicon` arrives as an ARGUMENT, which is what keeps this function pure
    # now that the table is per-user and lives in the database. Reading those
    # rows here would put I/O in routing — golden rule 4's exact defect. The
    # caller loads them (`deps.get_lexicon`); this applies them. Defaulting to
    # the shipped table means every existing caller and test is unaffected.
    # Only applied when transform.is_identity: normalization targets the
    # script the CHOSEN model actually receives, and is declared per-spec
    # (ModelSpec.text_normalizations), never globally for "Urdu".
    if transform.is_identity and chosen.text_normalizations:
        resolved_text, applied_normalizations = apply_text_normalizations(
            profile.text, chosen.text_normalizations, lexicon
        )
    else:
        # Identity transforms with no declared normalization pass through
        # unchanged. Non-identity transforms are filled in by the service
        # layer via with_resolved_text() — see needs_transform.
        resolved_text, applied_normalizations = profile.text, ()

    return RoutePlan(
        model_id=chosen.id,
        transform=transform,
        resolved_text=resolved_text,
        requested_language=profile.language,
        source_script=profile.script,
        rationale=_rationale(
            profile, chosen, transform,
            experimental=experimental, normalizations=applied_normalizations,
        ),
        alternatives=tuple(c.id for c in candidates if c.id != chosen.id),
        experimental=experimental,
        text_normalizations=tuple(n.value for n in applied_normalizations),
    )


@dataclass(frozen=True, slots=True)
class _Target:
    """The (language, script) a model must actually be able to render."""

    language: str
    script: Script


def _plan_transform(
    profile: TextProfile, strategy: UrduStrategy, catalog: CatalogView
) -> tuple[_Target, TextTransform]:
    """Decide the target pair and the transform needed to reach it."""
    lang, script = profile.language, profile.script
    # "hi" is not a `LanguageCode` member (Hindi was fully removed as a target
    # language) — used here as a plain string on purpose, so this target never
    # matches any catalog spec and `resolve()` correctly falls through to
    # `NoRouteError` rather than crashing on a deleted enum attribute. See the
    # module-level note on TRANSLITERATE and the Roman-Urdu fallback below:
    # both were already unreachable in practice before Hindi's removal.
    hindi = _Target("hi", Script.DEVANAGARI)

    if lang == LanguageCode.URDU.value:
        if script is Script.ARABIC:
            if strategy is UrduStrategy.TRANSLITERATE:
                # Two hops, and R4b measured it compounding errors badly:
                # مجھے -> "majhay" -> मझे rather than मुझे. Opt-in only.
                return hindi, TextTransform(
                    TransformKind.ARAB_TO_DEVA, Script.ARABIC, Script.DEVANAGARI,
                    lossy=True,
                )
            return (
                _Target(lang, Script.ARABIC),
                TextTransform(TransformKind.NONE, Script.ARABIC, Script.ARABIC),
            )
        if script is Script.LATIN:
            # Roman Urdu / Hinglish. A tokenizer-free model (VoxCPM2) renders
            # romanized Urdu DIRECTLY — verified 2026-08-05 that Roman input
            # sounds equivalent to Devanagari — so when a model natively serves
            # (ur, Latin) the text is passed through UNCHANGED. Only a model that
            # needs Devanagari (F5) falls back to the lossless one-hop below.
            if catalog.candidates(lang, Script.LATIN):
                return (
                    _Target(lang, Script.LATIN),
                    TextTransform(TransformKind.NONE, Script.LATIN, Script.LATIN),
                )
            return hindi, TextTransform(
                TransformKind.ROMAN_TO_DEVA, Script.LATIN, Script.DEVANAGARI
            )
        if script is Script.DEVANAGARI:
            raise NoRouteError(
                language=lang,
                script=script.value,
                supported=_supported(catalog),
                suggestion=(
                    "Urdu text detected as Devanagari script is not supported — "
                    "use Perso-Arabic (اردو) or Roman script."
                ),
            )

    return (
        _Target(lang, script),
        TextTransform(TransformKind.NONE, script, script),
    )


def _supported(catalog: CatalogView) -> tuple[tuple[str, str], ...]:
    return tuple((lang, script.value) for lang, script in catalog.supported_pairs())


def _suggest(profile: TextProfile) -> str | None:
    if profile.script is Script.UNKNOWN:
        return "The text contains no letters in a supported script."
    return None


#: User-facing clauses for each normalization kind, keyed by value (not the
#: enum itself — keeps this function importable without a domain.urdu_text
#: dependency at call sites that only pass through the plan).
_NORMALIZATION_CLAUSES: dict[str, str] = {
    "numbers": "numbers normalized to spoken Urdu words",
    "loanword_lexicon": "select English words respelled for pronunciation",
}


def _rationale(
    profile: TextProfile,
    spec: SpecView,
    transform: TextTransform,
    *,
    experimental: bool = False,
    normalizations: tuple = (),
) -> str:
    """User-facing prose for the route chip. Displayed verbatim."""
    name = getattr(spec, "display_name", spec.id)
    if transform.kind is TransformKind.ROMAN_TO_DEVA:
        base = f"Roman Urdu transliterated to Devanagari and rendered by {name}"
    elif transform.kind is TransformKind.ARAB_TO_DEVA:
        base = (
            f"Urdu transliterated from Perso-Arabic to Devanagari and rendered "
            f"by {name}. Short vowels are inferred, so pronunciation may differ."
        )
    else:
        base = f"{profile.language} in {profile.script.value} script rendered by {name}"
    if normalizations:
        clauses = ", ".join(_NORMALIZATION_CLAUSES[n.value] for n in normalizations)
        base = f"{base} ({clauses})"
    if experimental:
        return (
            f"{base} — EXPERIMENTAL: you explicitly picked this model despite it "
            f"not passing its own accuracy gate. Voice-identity match is known to "
            f"be weaker than the recommended model."
        )
    return base
