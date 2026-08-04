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

from dataclasses import dataclass
from enum import StrEnum

from ..inference.catalog import ModelCatalog
from .language import Script, TextProfile

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


def resolve(
    profile: TextProfile,
    requested: str | None,
    catalog: ModelCatalog,
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
        catalog: the model catalog. Carries no residency information.
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
    raise NotImplementedError("Wave 2 / B3")
