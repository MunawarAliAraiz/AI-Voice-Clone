"""
AI Voice Clone Studio — The model catalog.

CONTRACT MODULE. Wave 0.

Three runtimes, five specs. This is the single place where "what models exist"
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
from .spec import LanguageSupport, License, ModelSpec, RuntimeKind

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

F5_INDIC = ModelSpec(
    id="f5_indic",
    display_name="IndicF5 (Hindi)",
    runtime=RuntimeKind.F5,
    license=License.MIT,
    hf_repo="ai4bharat/IndicF5",
    # ⚠️ This revision is verified for the README ONLY. The repo is GATED
    # ("gated": "auto") and every other file 401s without an accepted license
    # and an HF_TOKEN. An earlier commit called this "verified from the
    # downloaded snapshot" — that was wrong; the snapshot directory existed but
    # held only the README. The weights have never been fetched.
    #
    # It is nonetheless a SECURITY CONTROL, not mere hygiene: R1b confirmed
    # IndicF5 ships its own model.py and requires
    # AutoModel.from_pretrained(..., trust_remote_code=True), i.e. arbitrary
    # code execution from the repo. The pin fixes WHICH code runs.
    hf_revision="ba85abedf18dc479a447eaa0eccbd76ab78a47d5",
    gated=True,
    languages=(
        LanguageSupport(language=LanguageCode.HINDI.value, script=Script.DEVANAGARI),
    ),
    vram_mb=4000,
    est_load_sec=40.0,
    needs_reference_text=True,
    reference_max_sec=12.0,
    notes=(
        "GATED — needs an accepted HF license and an HF_TOKEN before it can be "
        "downloaded at all. Loads via AutoModel(trust_remote_code=True) against "
        "a bundled model.py (architecture INF5Model, model_type inf5), so it "
        "needs a SEPARATE loader from the raw-checkpoint F5 path — one runtime "
        "class does NOT serve both. Nothing here is runtime-verified yet."
    ),
)

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
        LanguageSupport(language=LanguageCode.HINDI.value, script=Script.DEVANAGARI),
        LanguageSupport(language=LanguageCode.ENGLISH.value, script=Script.LATIN),
    ),
    vram_mb=6000,
    est_load_sec=30.0,
    needs_reference_text=False,
    params={
        # R2 confirms the real parameter names and ranges. These replace the
        # nine emotion presets, which resolved to an atempo multiplier and were
        # a no-op on Urdu and Hindi — the target languages.
        "exaggeration": {
            "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
            "title": "Expressiveness",
        },
        "cfg_weight": {
            "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
            "title": "Guidance",
        },
    },
    notes="R2: verify the advertised 23-language list; claims are not support.",
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
    # VoxCPM2 is tokenizer-free and renders ROMANIZED Hindi/Urdu directly (owner
    # confirmed Roman ~= Devanagari by ear), so it serves Latin-script hi/ur with
    # NO transliteration step — the whole ai4bharat hop was dropped. speaker_cosine
    # is ECAPA against public-domain references; CER for the Latin cells is not
    # re-measured (owner listening is authoritative, per the screen-not-verdict
    # rule), and the Devanagari CER is the Phase-A figure.
    languages=(
        LanguageSupport(language=LanguageCode.ENGLISH.value, script=Script.LATIN,
                        verified=True, speaker_cosine=0.795),
        LanguageSupport(language=LanguageCode.HINDI.value, script=Script.LATIN,
                        verified=True, speaker_cosine=0.887),
        LanguageSupport(language=LanguageCode.URDU.value, script=Script.LATIN,
                        verified=True, speaker_cosine=0.826),
        LanguageSupport(language=LanguageCode.HINDI.value, script=Script.DEVANAGARI,
                        verified=True, cer=0.086, speaker_cosine=0.887),
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


#: 3 runtimes, 4 specs. Was 5 until f5_openf5_en was dropped for licensing —
#: see the note where it used to be defined.
ALL_SPECS: tuple[ModelSpec, ...] = (
    F5_OPENBIBLE_URDU,
    F5_INDIC,
    CHATTERBOX_ML_V3,
    VOXCPM2,
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
