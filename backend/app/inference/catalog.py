"""
AI Voice Clone Studio — The model catalog.

CONTRACT MODULE. Wave 0.

Four runtimes, five specs. This is the single place where "what models exist"
is written down; nothing else may invent a model id.

STATUS OF THE VALUES IN THIS FILE
---------------------------------
The *shape* is final. Several *values* are provisional and are Phase A's
output, not Wave 0's:

    hf_revision      PENDING_PIN  -> R1/R2/R3 resolve to a 40-char commit sha
    hf_repo          may be PENDING_REPO where the plan did not name it exactly
    vram_mb          published figures, mostly measured on Ada; A5000 is sm_86
    est_load_sec     estimates
    LanguageSupport  verified=False everywhere until measured

`verified=False` means routing will not select the cell (see
`ModelSpec.supports`). So on a Wave 0 checkout the catalog resolves *nothing* —
that is deliberate and correct. A catalog that routes on unmeasured claims is
the same defect as a mock engine answering "auto", one layer up.

Wave 3 flips verified cells to True with real numbers and DELETES cells that
failed the gate. Do not pre-emptively set verified=True to make a test pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.language import LanguageCode, Script
from .spec import LanguageSupport, License, ModelSpec, RuntimeKind, TextNormalization

__all__ = ["PENDING_PIN", "PENDING_REPO", "ModelCatalog", "CATALOG", "build_catalog"]


#: Sentinel for an unresolved HF revision. `test_catalog.py` asserts no shipped
#: spec still carries it, so this cannot silently reach production.
PENDING_PIN = "PENDING_PIN"

#: Sentinel for a repo id the plan did not state exactly. Wave 1 research
#: replaces these with the verified repo; guessing here would produce a catalog
#: that 404s at download time and looks like a network fault.
PENDING_REPO = "PENDING_REPO"


# ── F5 runtime: three checkpoints, one process type ──────────────────────────

F5_OPENBIBLE_URDU = ModelSpec(
    id="f5_openbible_urdu",
    display_name="OpenBible Urdu (F5)",
    runtime=RuntimeKind.F5,
    license=License.CC_BY_SA_4_0,
    hf_repo="multilingual-tts/F5-TTS-OpenBible-Urdu",
    # Verified: this is the snapshot actually downloaded to the pod's HF cache.
    hf_revision="78b7eca91d22e2e7be7f3ca4778b8eb59dbaf465",
    languages=(
        LanguageSupport(language=LanguageCode.URDU.value, script=Script.ARABIC),
    ),
    # MEASURED, R1b. torch.cuda.max_memory_allocated() = 6112 MB; concurrent
    # nvidia-smi sampling caught a 5845 MiB process peak. Using the torch
    # figure as the conservative one.
    vram_mb=6112,
    # MEASURED: 7.0s cold (model + vocos vocoder). The 40s estimate was 5x
    # pessimistic — this is by far the fastest spec to load.
    est_load_sec=7.0,
    # MEASURED: 0.23 cold / 0.20 warm on a ~15-word Urdu sentence.
    est_rtf=0.21,
    needs_reference_text=True,
    # MEASURED by reading f5-tts 1.1.22 source, then CONFIRMED with a real
    # 23.8s clip (log: "Audio is over 12s, clipping short.", output still valid). The real limit is
    # ~12s, not the ~6s previously assumed, and it is SILENT: pydub silence-based
    # clipping accumulates to a 12000ms target, then hard-truncates aseg[:12000]
    # with only a print. No exception reaches the caller.
    #
    # The "8192 frame limit" in the plan is a DIFFERENT thing — dit.py's
    # precompute_max_pos, the rotary-position table (~87s of 24kHz audio), which
    # clamps rather than raising. It is not the reference limit.
    reference_max_sec=12.0,
    notes=(
        "The only free native-Urdu cloning checkpoint that exists. Trained from "
        "scratch, so it does NOT inherit F5's CC-BY-NC. Bible-domain read "
        "speech: formal register, narrow prosody — expect it to sound liturgical "
        "on conversational text. CC-BY-SA requires a NOTICE attribution entry. "
        "Raw model_last.pt state dict loaded via f5_tts.infer.utils_infer."
        "load_model — no custom code, no trust_remote_code. Also needs vocab.txt "
        "and a Hydra arch YAML. "
        "HIDDEN WHISPER: if ref_text is blank, preprocess_ref_audio_text() "
        "silently loads openai/whisper-large-v3-turbo to transcribe the "
        "reference. ALWAYS pass ref_text or the runtime pays an undeclared "
        "model load and per-request ASR cost."
    ),
)

# f5_indic — REMOVED (Hindi support fully retired from the catalog). Existed
# solely to serve Hindi (`(hi, Script.DEVANAGARI)`, gated repo, never actually
# fetched — see git history if the trust_remote_code security-control
# reasoning is ever needed again for a different gated repo).

# f5_openf5_en — DROPPED (R1b, 2026-08-04).
#
# No genuinely permissive English F5 checkpoint exists. R1b searched ~100 HF F5
# repos; every English-capable one traces back to SWivid/F5-TTS, whose weights
# are CC-BY-NC-4.0. Several carry a permissive tag that covers only a format
# conversion, not the weights:
#
#   lucasnewman/f5-tts-mlx (mit)     — its own README says the weights are
#                                      reshaped from SWivid/F5-TTS. MIT covers
#                                      the conversion code only.
#   H5N1AIDS/F5-TTS-ONNX (apache-2)  — empty README, no provenance
#   kevinwang676/F5-TTS (mit)        — empty README, no provenance
#   zeeshiii05/E2-F5-TTS (apache-2)  — dataset tag is a math-reasoning corpus;
#                                      not a credible TTS checkpoint
#
# This is license-washing, and golden rule #6 exists precisely for it: a
# permissive tag on top of CC-BY-NC weights does not make them shippable.
#
# English therefore routes to chatterbox_ml_v3 (MIT), which the plan already
# anticipated as the English option.


# ── Chatterbox runtime ───────────────────────────────────────────────────────

CHATTERBOX_ML_V3 = ModelSpec(
    id="chatterbox_ml_v3",
    display_name="Chatterbox Multilingual v3",
    runtime=RuntimeKind.CHATTERBOX,
    license=License.MIT,
    hf_repo="ResembleAI/chatterbox",
    # Verified via the HF X-Repo-Commit header. License MIT, confirmed
    # independently on the model card, GitHub, and PyPI (chatterbox-tts 0.1.7).
    hf_revision="5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
    languages=(
        LanguageSupport(language=LanguageCode.ENGLISH.value, script=Script.LATIN),
    ),
    vram_mb=6000,
    est_load_sec=30.0,
    needs_reference_text=False,
    params={
        # R2 confirms the real parameter names and ranges. These replace the
        # nine emotion presets, which resolved to an atempo multiplier and were
        # a no-op on the target languages.
        "exaggeration": {
            "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
            "title": "Expressiveness",
        },
        "cfg_weight": {
            "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
            "title": "Guidance",
        },
    },
    notes=(
        "R2: verify the advertised 23-language list; claims are not support. "
        "Phase 4c ran a real gate + human listen (2026-08-11) and the owner's "
        "own verdict was 'not that good but ok, identity is matched around "
        "60%' — see docs/PHASE_A_RESULTS.md. Listed in the picker as an "
        "explicit, labeled experimental choice (experimental_listing=True) "
        "despite that; Auto-routing never selects it."
    ),
    caveat="Voice identity matches roughly 60%; Urdu is not supported.",
    experimental_listing=True,
)


# ── VoxCPM runtime ───────────────────────────────────────────────────────────

VOXCPM2 = ModelSpec(
    id="voxcpm2",
    display_name="VoxCPM 2",
    runtime=RuntimeKind.VOXCPM,
    license=License.APACHE_2_0,
    hf_repo="openbmb/VoxCPM2",
    # Verified: snapshot actually downloaded to the pod's HF cache, which also
    # resolves the repo id itself.
    hf_revision="bffb3df5a29440629464e5e839f4d214c8714c3d",
    # VERIFIED 2026-08-05 by the E1/E2 cross-lingual tests + the owner's ear.
    # VoxCPM2 is tokenizer-free and renders ROMANIZED Urdu directly (owner
    # confirmed by ear), so it serves Latin-script Urdu with NO transliteration
    # step — the whole ai4bharat hop was dropped. speaker_cosine is ECAPA
    # against public-domain references; CER for the Latin cell is not
    # re-measured (owner listening is authoritative, per the screen-not-verdict
    # rule).
    languages=(
        LanguageSupport(language=LanguageCode.ENGLISH.value, script=Script.LATIN,
                        verified=True, speaker_cosine=0.795),
        LanguageSupport(language=LanguageCode.URDU.value, script=Script.LATIN,
                        verified=True, speaker_cosine=0.826),
    ),
    # MEASURED on the A5000 (sm_86), R3b. 6.2 GB resident after load, 7.3 GB
    # peak during generation per nvidia-smi (the real total, including CUDA
    # context — not torch.cuda.max_memory_allocated).
    vram_mb=7300,
    # MEASURED: 123.8s TRUE cold. 65.6s once torch Inductor's compile cache is
    # warm on disk. The estimate here was 45s — the UI would have promised
    # "~45s" and delivered two minutes.
    est_load_sec=124.0,
    # MEASURED: 0.57-0.58 English and Hindi. 1.9x the published 0.30, which was
    # an Ada figure; Ampere has no FP8. Still passes the RTF < 1.0 gate.
    est_rtf=0.58,
    needs_reference_text=False,
    params={
        "cfg_value": {
            "type": "number", "minimum": 1.0, "maximum": 3.0, "default": 2.0,
            "title": "Guidance",
        },
        "inference_timesteps": {
            "type": "integer", "minimum": 1, "maximum": 50, "default": 10,
            "title": "Quality steps",
        },
    },
    notes=(
        "Apache-2.0 on BOTH weights and code; model card explicitly permits "
        "commercial use. Native rates: 16 kHz encoder in, 48 kHz out. "
        "WARM-UP TRAP: the bundled warm-up (optimize=True) never passes a "
        "reference, so it does not compile the cloning path — the first real "
        "clone then pays an extra 40-55s torch.compile. The runtime MUST warm "
        "up with a real reference clip or every cold worker's first request "
        "looks broken. Reference duration does not affect output length "
        "(2s/10s/60s all fine, no truncation)."
    ),
)


VOXCPM2_URDU_ARABIC = ModelSpec(
    id="voxcpm2_urdu_arabic",
    display_name="VoxCPM 2 (Urdu, اردو script)",
    runtime=RuntimeKind.VOXCPM,
    license=License.APACHE_2_0,
    # Same checkpoint as VOXCPM2, no LoRA — this is a second catalog entry for
    # an unverified cell on an otherwise-verified spec, not a different
    # download. `ModelSpec` has no per-cell experimental flag (see spec.py's
    # `experimental_listing` docstring), so a spec that is mostly VERIFIED
    # (VOXCPM2's en/hi/ur-latin cells) cannot also carry one unverified
    # experimental cell without mislabeling the whole listing — hence a
    # second spec, exactly the pattern this module's own docstring names
    # ("one runtime, many specs").
    hf_repo=VOXCPM2.hf_repo,
    hf_revision=VOXCPM2.hf_revision,
    # Urdu bake-off arm B (docs/URDU_BAKEOFF_RESULTS.md), 2026-08-14. Perso-
    # Arabic input DIRECTLY, base checkpoint, no transform and no fine-tune.
    #
    # A Urdu LoRA fine-tune (arm D, spec id voxcpm2_urdu_lora) was shipped
    # from this same bake-off on 2026-08-14 and WITHDRAWN the next day: on
    # real use through the app the owner judged base VoxCPM2 better than the
    # LoRA, which overrides arm D's blind-listen median under this project's
    # own "owner listening is authoritative" rule. This spec is what's left
    # once the fine-tune comes back out — the base checkpoint, still
    # unverified on this cell, still worth offering explicitly.
    #
    # [BENCH]: CER 0.1887 (owner) / 0.0385 (female) — both clear the <0.25
    # gate. Speaker cosine 0.6664 (owner) / 0.7489 (female) — the owner
    # reference is BELOW this catalog's 0.70 gate, so `verified=False` here.
    #
    # [LISTEN] blind listening, one rater, n=26 (13 sentences x 2 references):
    # pronunciation 3.0/5, naturalness 3.0/5, speaker identity 4.0/5,
    # code-switching 5.0/5 — mediocre pronunciation, but a real working
    # answer for Perso-Arabic Urdu with no additional engineering, while
    # OmniVoice (arm E, [BENCH]/[LISTEN] pronunciation 5.0/5, but CC-BY-NC —
    # see docs/URDU_MODEL_LICENSING.md) is integrated as its own runtime.
    languages=(
        LanguageSupport(
            language=LanguageCode.URDU.value, script=Script.ARABIC,
            verified=False, cer=0.1887, speaker_cosine=0.6664,
        ),
    ),
    vram_mb=VOXCPM2.vram_mb,
    est_load_sec=VOXCPM2.est_load_sec,
    est_rtf=VOXCPM2.est_rtf,
    needs_reference_text=False,
    params=VOXCPM2.params,
    experimental_listing=True,
    notes=(
        "Base VoxCPM2, no LoRA, Perso-Arabic Urdu input direct (bake-off arm "
        "B). Mediocre but working: 3.0/5 pronunciation, 3.0/5 naturalness, "
        "4.0/5 identity, 5.0/5 code-switching — see "
        "docs/URDU_BAKEOFF_RESULTS.md. Replaces voxcpm2_urdu_lora, withdrawn "
        "2026-08-15 on the owner's real-use verdict that base sounded better "
        "than the fine-tune; see catalog history for that spec's numbers."
    ),
    caveat="Pronunciation and naturalness are mediocre (3/5).",
)


#: ── OmniVoice runtime ────────────────────────────────────────────────────────

OMNIVOICE_URDU = ModelSpec(
    id="omnivoice_urdu",
    display_name="OmniVoice (Urdu)",
    runtime=RuntimeKind.OMNIVOICE,
    # CC-BY-NC WEIGHTS (Apache-2.0 code — checked separately, see
    # docs/URDU_MODEL_LICENSING.md's "trap this report exists to catch").
    # Permitted here for the owner's personal use behind VCS_API_KEY, per
    # golden rule 6 as amended 2026-08-15 — never for a shipped product.
    license=License.CC_BY_NC,
    hf_repo="k2-fsa/OmniVoice",
    # Resolved on the pod at first fetch during the bake-off (2026-08-14) —
    # recorded from both arm-E manifests (owner and female reference), not
    # guessed. `test_all_revisions_pinned` requires this be a real 40-char sha.
    hf_revision="c5fdb5ccb189668d56333f77ba2629f4cd7535f4",
    # Urdu bake-off arm E (docs/URDU_BAKEOFF_RESULTS.md), 2026-08-14. 211.27 h
    # of native Urdu training data (more than its Hindi, 117.17 h), 0.6B params.
    #
    # [BENCH], arm E (eval harness's own loader), 2026-08-14: CER 0.1489
    # (owner) / 0.0851 (female), speaker cosine 0.7366 (owner) / 0.7938
    # (female) — both clear this catalog's <0.25 CER / >0.70 cosine gates.
    #
    # [BENCH], arm Eprod (2026-08-15) — RE-RUN AGAINST THE ACTUAL PRODUCTION
    # `OmniVoiceBackend` class below, not the eval harness's own separate
    # loader, closing exactly the gap the note below used to flag: CER 0.0800
    # (owner) / 0.0400 (female) — roughly HALVED — speaker cosine 0.7450
    # (owner) / 0.8158 (female) — both slightly better than arm E's numbers.
    # Both gates clear comfortably on both references, on the real runtime
    # this catalog actually dispatches to. `cer`/`speaker_cosine` below are
    # the arm Eprod (production-backend) numbers.
    #
    # Still `verified=False`: the numeric gate is a SCREEN, not a verdict —
    # this project's own rule (VoxCPM2 once passed CER and still sounded like
    # a stranger). A fresh owner listen against these specific arm Eprod
    # clips is the remaining step before this flips.
    #
    # [LISTEN] blind listening, one rater, n=26 (13 sentences x 2 references),
    # against arm E's (pre-production-backend) clips: pronunciation 5.0/5 —
    # the single best result of the whole bake-off, at both references.
    # Naturalness 4.0/5, identity 4.0/5. Weakest axis: code-switching 3.0/5,
    # the worst of any arm — worth specific attention now that
    # docs/URDU_BAKEOFF_RESULTS.md's Phase 0 code-switch routing fix
    # (profile_text's Latin-island rescue) makes code-switched Urdu reach
    # this spec at all. Not yet re-listened against arm Eprod specifically.
    languages=(
        LanguageSupport(
            language=LanguageCode.URDU.value, script=Script.ARABIC,
            verified=False, cer=0.0800, speaker_cosine=0.7450,
        ),
    ),
    # MEASURED, bake-off: 4699 MB (female) peak, the LOWEST of any arm —
    # using the higher of the two references, rounded up.
    vram_mb=4700,
    # MEASURED on the pod (2026-08-15), production runtime path
    # (`OmniVoiceBackend.load()`, not the eval driver's loader): 159.3s wall,
    # weights already warm in /workspace/hf-cache so this is pure model
    # construction (deserializing ~840 weight tensors onto the GPU across two
    # components), not network time. Note a first-`synth()` call pays an
    # ADDITIONAL ~17s beyond steady state: OmniVoice lazily loads an embedded
    # Whisper sub-model (587 more shards) the first time `ref_text` isn't
    # supplied, to auto-transcribe the reference audio. Passing `ref_text`
    # up front should avoid that cost — not yet verified.
    est_load_sec=159.3,
    # MEASURED, bake-off: 0.736 (female) RTF — using the higher (worse-case)
    # of the two references; the owner reference measured 0.442.
    est_rtf=0.736,
    needs_reference_text=False,
    experimental_listing=True,
    notes=(
        "CC-BY-NC weights (Apache-2.0 code) — personal use only, see "
        "docs/URDU_MODEL_LICENSING.md. Bake-off arm E: best pronunciation of "
        "any arm (5.0/5) and the only Urdu cell whose automated gate passes "
        "on both references. 2026-08-15: re-run as arm Eprod through the "
        "actual production `OmniVoiceBackend` class — CER roughly halved "
        "(0.0800 owner / 0.0400 female vs arm E's 0.1489 / 0.0851), cosine "
        "slightly better (0.7450 / 0.8158 vs 0.7366 / 0.7938). Both gates "
        "clear comfortably on the real dispatch target, but verified=False "
        "stays until a fresh owner listen against these specific clips — a "
        "numeric pass is a screen, not a verdict. "
        "Weakest on code-switching (3.0/5, scored against arm E's clips) — "
        "see docs/URDU_BAKEOFF_RESULTS.md."
    ),
    caveat="Best pronunciation so far, but non-commercial (personal use only).",
    # Eval-verified, OmniVoice-specific (docs/URDU_BAKEOFF_RESULTS.md SS5c/SS5d):
    # raw digits are mispronounced regardless of script (ASCII or Eastern
    # Arabic-Indic) — NUMBERS expands them to spoken Urdu words. URL and
    # database each needed an individually-verified respelling — see
    # domain/urdu_text.py's lexicon; LOANWORD_LEXICON is exactly those 2
    # entries, not a general English-transliteration rule. Neither
    # normalization has been tested against any other spec — do not copy
    # this tuple onto voxcpm2_urdu_arabic or anything else without the same
    # per-spec verify-by-ear evidence.
    text_normalizations=(TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON),
)


#: 4 runtimes, 5 specs. f5_indic was removed along with all Hindi support
#: (Hindi is no longer a target language — see CLAUDE.md); f5_openf5_en was
#: removed earlier for licensing (see the note where it used to be defined).
#: The personal LoRA fine-tune (voxcpm2_urdu_lora) held a slot 2026-08-14 to
#: 2026-08-15, then was withdrawn on the owner's real-use verdict — base
#: VoxCPM2 sounded better than the fine-tune, overriding the blind-listen
#: median under this project's own "owner listening is authoritative" rule
#: (see git history and docs/URDU_BAKEOFF_RESULTS.md §5 for the full
#: account). VOXCPM2_URDU_ARABIC holds that slot now: the same base
#: checkpoint, still explicitly experimental, so Perso-Arabic Urdu keeps a
#: working answer. OMNIVOICE_URDU is a 4th runtime, added 2026-08-15
#: (bake-off arm E, the single best pronunciation result of the whole
#: bake-off) — CC-BY-NC, so it is legal here only under golden rule 6's
#: personal-use amendment.
ALL_SPECS: tuple[ModelSpec, ...] = (
    F5_OPENBIBLE_URDU,
    CHATTERBOX_ML_V3,
    VOXCPM2,
    VOXCPM2_URDU_ARABIC,
    OMNIVOICE_URDU,
)


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """
    An immutable view over the registered specs.

    Constructed once at startup and passed to `routing.resolve` as a value.
    Holds no residency information — see the note in `ModelSpec`.
    """

    specs: tuple[ModelSpec, ...]

    def __post_init__(self) -> None:
        ids = [s.id for s in self.specs]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate model ids in catalog: {dupes}")

    def get(self, model_id: str) -> ModelSpec | None:
        """Look up a spec by id, or None."""
        return next((s for s in self.specs if s.id == model_id), None)

    def require(self, model_id: str) -> ModelSpec:
        """Look up a spec by id, raising if unknown."""
        spec = self.get(model_id)
        if spec is None:
            raise KeyError(f"unknown model id: {model_id!r}")
        return spec

    def candidates(self, language: str, script: Script) -> tuple[ModelSpec, ...]:
        """
        Specs that natively and *verifiably* handle (language, script).

        Ordering is catalog order, which is preference order. Note this consults
        no load state whatsoever — a cold spec ranks exactly as high as a
        resident one. Preferring whatever happens to be loaded is how the
        predecessor picked mock every time.
        """
        return tuple(s for s in self.specs if s.supports(language, script))

    def by_runtime(self, runtime: RuntimeKind) -> tuple[ModelSpec, ...]:
        """All specs sharing a worker process type."""
        return tuple(s for s in self.specs if s.runtime is runtime)

    def supported_pairs(self) -> tuple[tuple[str, Script], ...]:
        """
        Every (language, script) the catalog can actually serve.

        This is what a 422 body offers the user when their request is
        unroutable, so it must never include unverified cells.
        """
        pairs: list[tuple[str, Script]] = []
        for spec in self.specs:
            for ls in spec.languages:
                if ls.verified and (ls.language, ls.script) not in pairs:
                    pairs.append((ls.language, ls.script))
        return tuple(pairs)

    def unshippable(self) -> tuple[ModelSpec, ...]:
        """Specs whose license forbids shipping. Wave 4's audit asserts this is empty."""
        return tuple(s for s in self.specs if not s.license.is_permissive)

    def needs_attribution(self) -> tuple[ModelSpec, ...]:
        """Specs requiring a NOTICE entry (CC-BY-SA)."""
        return tuple(s for s in self.specs if s.license.requires_attribution)


def build_catalog(specs: tuple[ModelSpec, ...] = ALL_SPECS) -> ModelCatalog:
    """Build the catalog. Parameterized so tests can supply their own specs."""
    return ModelCatalog(specs=specs)


#: The process-wide catalog.
CATALOG: ModelCatalog = build_catalog()
