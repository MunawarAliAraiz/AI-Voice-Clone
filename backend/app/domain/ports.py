"""
AI Voice Clone Studio — Ports the domain layer needs from the outside.

CONTRACT MODULE. Wave 0.

WHY THIS EXISTS
---------------
`routing.resolve()` needs to ask a catalog "which specs serve this pair?". The
obvious implementation imports `inference.catalog.ModelCatalog` — and that is a
circular import, because `inference.spec` already imports `domain.language.Script`.

The cycle is not just a mechanical annoyance; it points at a layering mistake.
The dependency arrow must run ONE WAY:

    inference  ──depends on──>  domain

Domain is the pure layer. It may not know that an inference layer exists. So
instead of importing the concrete catalog, routing depends on these structural
protocols, and `ModelCatalog` satisfies them without either module importing the
other.

Concretely: it also means `resolve()` can be tested against a five-line stub
catalog, with no `ModelSpec`, no licenses, and no HuggingFace ids in sight.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .language import Script
from .urdu_text import TextNormalization

__all__ = ["SpecView", "CatalogView"]


@runtime_checkable
class SpecView(Protocol):
    """
    The slice of a model spec that routing is allowed to see.

    Deliberately tiny. Routing needs an identity and a capability check — it has
    no business knowing about VRAM, licenses, or load times, and giving it access
    to those invites decisions that belong to the scheduler.

    Note what is absent, again: any notion of being loaded. Routing that consults
    residency is the defect this whole design removes.
    """

    @property
    def id(self) -> str: ...

    def supports(self, language: str, script: Script) -> bool:
        """True only for pairs verified by Phase A."""
        ...

    def supports_experimental(self, language: str, script: Script) -> bool:
        """True for a claimed pair on a spec explicitly opted into listing
        despite being unverified. See `ModelSpec.supports_experimental`."""
        ...

    @property
    def text_normalizations(self) -> tuple[TextNormalization, ...]:
        """Pronunciation fixes declared for THIS spec. See `ModelSpec`'s field."""
        ...


@runtime_checkable
class CatalogView(Protocol):
    """The slice of the catalog that routing is allowed to see."""

    def candidates(self, language: str, script: Script) -> tuple[SpecView, ...]:
        """
        Specs that verifiably serve this pair, in preference order.

        Preference order is CATALOG order — not "whatever is loaded". Ranking a
        resident model above the correct one is exactly how the predecessor
        picked mock every time.
        """
        ...

    def get(self, model_id: str) -> SpecView | None:
        """Look up an explicitly requested spec, or None if unknown."""
        ...

    def supported_pairs(self) -> tuple[tuple[str, Script], ...]:
        """
        Every (language, script) that can actually be served.

        This is what a NoRouteError offers the user, so it must contain only
        verified pairs.
        """
        ...
