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

from dataclasses import dataclass, replace
from enum import StrEnum

from ..exceptions import AmbiguousScriptError, ModelNotFoundError, NoRouteError
from .language import LanguageCode, Script, TextProfile
from .ports import CatalogView, SpecView

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

    Exists because no truly-free model does Urdu zero-shot cloning *well*. The
    one free native checkpoint (OpenBible-Urdu, CC-BY-SA) is Bible-domain read
    speech — accurate but liturgical. Routing Urdu through a stronger Hindi
    model via transliteration is often better on conversational text, and is
    linguistically sound: Hindi and Urdu are the same spoken language.

    That is a taste judgement, so it is the user's to make, not a default we
    silently impose.
    """

    #: Perso-Arabic goes to the native Urdu checkpoint. The default.
    NATIVE = "native"
    #: Perso-Arabic is transliterated to Devanagari and rendered by a Hindi
    #: model. Lossy; `RoutePlan.lossy` is set so the UI can say so.
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
        ur + Perso-Arabic (TRANSLITERATE) -> best Hindi spec, ARAB_TO_DEVA (lossy)
        ur + Latin  (Roman Urdu)          -> best Hindi spec, ROMAN_TO_DEVA
        hi + Devanagari                   -> best Hindi spec, transform NONE
        en + Latin                        -> best English spec, transform NONE
        anything else                     -> NoRouteError

    Two pairs are rejected on purpose rather than accommodated:

      * (ur, DEVANAGARI) — that is Hindi's script. Raise, and suggest
        language="hi". Accepting it would quietly make the language field
        meaningless.
      * (en, ARABIC) and friends — no spec claims them.

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

    if requested is not None:
        spec = catalog.get(requested)
        if spec is None:
            raise ModelNotFoundError(requested)
        # An explicit request is honored or refused. It is NEVER silently
        # swapped — that substitution is the whole defect being designed out.
        if not spec.supports(target.language, target.script):
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

    return RoutePlan(
        model_id=chosen.id,
        transform=transform,
        # Identity transforms are already resolved. Anything else is filled in
        # by the service layer via with_resolved_text() — see needs_transform.
        resolved_text=profile.text,
        requested_language=profile.language,
        source_script=profile.script,
        rationale=_rationale(profile, chosen, transform),
        alternatives=tuple(c.id for c in candidates if c.id != chosen.id),
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
    hindi = _Target(LanguageCode.HINDI.value, Script.DEVANAGARI)

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
            # Roman Urdu. R4b verified this is a DIRECT one hop, and that the
            # Devanagari output is more accurate than the Perso-Arabic output
            # from the same Roman input — so this is the better path, not a
            # licensing compromise.
            return hindi, TextTransform(
                TransformKind.ROMAN_TO_DEVA, Script.LATIN, Script.DEVANAGARI
            )
        if script is Script.DEVANAGARI:
            raise NoRouteError(
                language=lang,
                script=script.value,
                supported=_supported(catalog),
                suggestion="That is Devanagari — did you mean language='hi'?",
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


def _rationale(
    profile: TextProfile, spec: SpecView, transform: TextTransform
) -> str:
    """User-facing prose for the route chip. Displayed verbatim."""
    name = getattr(spec, "display_name", spec.id)
    if transform.kind is TransformKind.ROMAN_TO_DEVA:
        return f"Roman Urdu transliterated to Devanagari and rendered by {name}"
    if transform.kind is TransformKind.ARAB_TO_DEVA:
        return (
            f"Urdu transliterated from Perso-Arabic to Devanagari and rendered "
            f"by {name}. Short vowels are inferred, so pronunciation may differ."
        )
    return f"{profile.language} in {profile.script.value} script rendered by {name}"
