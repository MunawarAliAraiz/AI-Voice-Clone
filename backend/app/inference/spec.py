"""
AI Voice Clone Studio — Model specifications.

CONTRACT MODULE. Wave 0. Do not add behavior here.

This module must remain importable on a machine with no GPU and no torch.
It describes *what models exist and what they claim*; it never loads one.

Terminology, because the distinction is load-bearing:

    runtime  — a Python process + dependency set capable of running a family of
               checkpoints (e.g. "f5"). Switching runtimes costs 20-60s.
    spec     — one concrete checkpoint with its own license, languages, and
               quality (e.g. "f5_openbible_urdu"). Swapping specs *within* a
               warm runtime costs 1-3s.

One runtime, many specs. Collapsing specs into a single opaque entry is exactly
the lie the predecessor code told when it advertised Urdu on an engine that
could not speak it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..domain.language import Script

__all__ = [
    "RuntimeKind",
    "License",
    "ModelState",
    "LanguageSupport",
    "ModelSpec",
]


class RuntimeKind(StrEnum):
    """A worker process type. Each has its own interpreter and dependency set."""

    F5 = "f5"
    CHATTERBOX = "chatterbox"
    VOXCPM = "voxcpm"

    #: Test-only. Produces deterministic silence, never audio that could be
    #: mistaken for a clone. Gated behind VCS_ALLOW_FAKE_RUNTIME.
    FAKE = "fake"


class License(StrEnum):
    """
    Weight licenses.

    Only permissive entries may appear in the shipped catalog. `CC_BY_NC` and
    `RESEARCH_ONLY` exist solely so the license audit in Wave 4 can *name* what
    it rejected — no shipped spec may carry them.
    """

    MIT = "MIT"
    APACHE_2_0 = "Apache-2.0"
    CC_BY_SA_4_0 = "CC-BY-SA-4.0"

    # Non-shippable. Present for the audit's vocabulary only.
    CC_BY_NC = "CC-BY-NC"
    RESEARCH_ONLY = "research-only"

    @property
    def is_permissive(self) -> bool:
        """True if weights under this license may ship in this product."""
        return self in _PERMISSIVE

    @property
    def requires_attribution(self) -> bool:
        """True if NOTICE must carry an attribution entry for these weights."""
        return self is License.CC_BY_SA_4_0


_PERMISSIVE: frozenset[License] = frozenset(
    {License.MIT, License.APACHE_2_0, License.CC_BY_SA_4_0}
)


class ModelState(StrEnum):
    """
    Residency of a spec, as reported to the UI.

    The UI uses this plus `est_load_sec` to tell the user "~40s to load"
    *before* they click, rather than after.
    """

    #: Loaded in a live worker. Generation starts immediately.
    RESIDENT = "resident"
    #: Its runtime has a live worker, but this checkpoint is not the loaded one.
    #: Costs a checkpoint swap (seconds), not a process start.
    WARM = "warm"
    #: No live worker for this runtime. Costs a process start plus a load.
    COLD = "cold"
    #: Weights are not on disk. Costs a download first.
    NOT_DOWNLOADED = "not_downloaded"


@dataclass(frozen=True, slots=True)
class LanguageSupport:
    """
    One (language, script) pair a spec claims to handle natively.

    A spec claims `("ur", Script.ARABIC)` only if it can speak Urdu written in
    Perso-Arabic *without* a transliteration step. Anything requiring a
    transform is a routing decision, not a model capability, and belongs in
    `domain.routing` — never here.

    `verified` is False until Phase A measures it. Wave 3 flips verified cells
    to True and *deletes* the ones that failed the gate. Nothing may be
    advertised to a user while `verified is False`.
    """

    language: str
    script: Script
    verified: bool = False
    #: Character error rate from Phase A (Whisper-large-v3). Gate: < 0.25.
    cer: float | None = None
    #: Speaker cosine similarity from Phase A. Gate: > 0.70.
    speaker_cosine: float | None = None

    def meets_gate(self) -> bool:
        """True if Phase A measured this cell and it passed all thresholds."""
        if self.cer is None or self.speaker_cosine is None:
            return False
        return self.cer < 0.25 and self.speaker_cosine > 0.70


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """
    One checkpoint. Immutable: the catalog is a constant, not mutable state.

    Deliberately absent: `is_loaded`. Residency is scheduler state, and mixing
    it into the thing routing reads is the precise defect that made a cold
    server answer with a sine wave. Routing sees this object; only the
    scheduler knows what is resident.
    """

    #: Stable identifier used on the wire and in the DB. Never renamed.
    id: str
    display_name: str
    runtime: RuntimeKind
    license: License

    #: HuggingFace repo and a PINNED revision. An unpinned revision combined
    #: with trust_remote_code is a supply-chain hole; the catalog test enforces
    #: that every revision here is a 40-char commit sha.
    hf_repo: str
    hf_revision: str

    languages: tuple[LanguageSupport, ...]

    #: Peak VRAM while generating, MB. Provisional until Phase A measures it;
    #: the scheduler's admission decisions depend on this being honest.
    vram_mb: int
    #: Cold start: process spawn + weight load, seconds. Shown in the UI.
    est_load_sec: float
    #: Real-time factor. < 1.0 means faster than realtime. Gate: < 1.0.
    est_rtf: float | None = None

    #: Some runtimes need a transcript of the reference audio (F5 does).
    needs_reference_text: bool = False
    #: Reference audio is hard-trimmed to this many seconds by the runtime.
    #: F5's 8192-frame limit lands here. The UI's trim editor exists because
    #: *which* seconds get used must not be arbitrary.
    reference_max_sec: float | None = None

    #: Runtime-specific generation knobs, name -> JSON schema fragment. The UI
    #: renders only the controls the selected spec actually declares, instead
    #: of showing nine emotion presets that resolve to an atempo multiplier.
    params: dict[str, dict] = field(default_factory=dict)

    #: True if the HF repo is access-gated: downloading needs an accepted
    #: license and an HF_TOKEN. A gated spec cannot be fetched on a fresh pod
    #: without human action, so the scheduler must fail with a clear,
    #: actionable error rather than a bare 401 from deep inside a loader.
    gated: bool = False

    #: True once Phase A has produced audible proof on the target GPU.
    phase_a_verified: bool = False
    notes: str = ""

    def supports(self, language: str, script: Script) -> bool:
        """
        True if this spec natively handles (language, script).

        Unverified cells return False. A model may not be routed to on the
        strength of a README claim.
        """
        return any(
            ls.language == language and ls.script is script and ls.verified
            for ls in self.languages
        )

    def claims(self, language: str, script: Script) -> bool:
        """
        True if this spec *claims* (language, script), verified or not.

        For diagnostics and the Phase A harness only. Never call this from
        routing — that is how unproven claims reach users.
        """
        return any(
            ls.language == language and ls.script is script for ls in self.languages
        )

    @property
    def verified_languages(self) -> tuple[str, ...]:
        """Distinct language codes this spec may actually be routed to."""
        return tuple(dict.fromkeys(ls.language for ls in self.languages if ls.verified))
