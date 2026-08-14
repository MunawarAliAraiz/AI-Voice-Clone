"""
Urdu bake-off — SYNTHESIS pass. Scoring is a separate process; see below.

Runs ONE arm and writes wavs plus a manifest. Split from scoring on purpose:
each model family lives in its own venv with a deliberately conflicting
dependency stack (see `[tool.uv] conflicts` in backend/pyproject.toml), and
`.venv-eval` carries yet another (transformers ASR + speechbrain). Loading a
model and the scorer in one interpreter is exactly the resolution failure that
killed the predecessor project. So:

    # in the model's own venv, once per arm x reference
    .venv-voxcpm/bin/python eval/run_urdu_bakeoff.py --arm B \
        --reference eval/fixtures/voice_urdu.wav --reference-id male

    # then, once, in .venv-eval
    .venv-eval/bin/python eval/score_urdu_bakeoff.py

Every arm reads its text from eval/fixtures/urdu_corpus.json via
eval/urdu_represent.py, so "same corpus everywhere" is structural rather than
a thing to remember.

WHAT THIS SCRIPT DOES NOT DECIDE
--------------------------------
Nothing. It generates audio and records numbers. CER and speaker cosine are a
SCREEN for gross breakage; the decision comes from blind listening (see
eval/build_listen_page.py). No `verified=True` may be set from anything here —
that rule exists because VoxCPM2 once passed CER and still sounded like a
stranger.

A failing arm is RECORDED, never dropped: if a model cannot be loaded or a
representation cannot be fed to it, the manifest gets a row with
`status: "could_not_run"` and the real reason. An arm silently missing from the
results would read as "we tested it and it was fine".
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from urdu_represent import Representation, load_corpus, render  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff"

# Pinned revisions. An unpinned revision plus trust_remote_code is a
# supply-chain hole (golden rule 7); IndicF5 requires trust_remote_code, so its
# pin is a security control, not a reproducibility nicety.
VOXCPM_REPO, VOXCPM_REV = "openbmb/VoxCPM2", "bffb3df5a29440629464e5e839f4d214c8714c3d"
INDICF5_REPO, INDICF5_REV = "ai4bharat/IndicF5", "ba85abedf18dc479a447eaa0eccbd76ab78a47d5"
# Revisions for these two are resolved on the pod at first fetch and written
# into the manifest — they are NOT guessed here. See `_resolve_revision`.
OMNIVOICE_REPO = "k2-fsa/OmniVoice"
HIGGS_REPO = "bosonai/higgs-audio-v3-tts-4b"


@dataclass(frozen=True, slots=True)
class Arm:
    id: str
    model: str
    representation: str
    lora: bool = False
    question: str = ""


#: Arm G from the plan is deliberately NOT here. "Best candidate x code-switched
#: text" is a reporting SLICE over items whose `stresses` contain "code_switch"
#: (owner_04, owner_05, technical, abbreviations), which every arm already
#: generates. Making it a separate synthesis run would regenerate identical
#: audio under a different name and invite the two copies to disagree.
ARMS: dict[str, Arm] = {
    "A": Arm("A", "voxcpm2", Representation.ROMAN,
             question="Today's production baseline."),
    "B": Arm("B", "voxcpm2", Representation.PERSO_ARABIC,
             question="Does native script help, or does VoxCPM2 read it as Arabic?"),
    "C": Arm("C", "voxcpm2", Representation.DEVANAGARI,
             question="Does the Hindustani-equivalence route work on VoxCPM2?"),
    "D": Arm("D", "voxcpm2", Representation.PERSO_ARABIC, lora=True,
             question="Does the existing LoRA help, fed its own training script?"),
    "E": Arm("E", "omnivoice", Representation.PERSO_ARABIC,
             question="211h of native Urdu training data, 0.6B."),
    "F": Arm("F", "higgs_v3", Representation.PERSO_ARABIC,
             question="Urdu in the production-quality tier, PK-flagged."),
    "H": Arm("H", "indicf5", Representation.PERSO_ARABIC,
             question="Can IndicF5's tokenizer accept raw Urdu at all?"),
    "I": Arm("I", "indicf5", Representation.DEVANAGARI_PLAIN,
             question="THE central question: Pakistani Urdu via plain Devanagari?"),
    "J": Arm("J", "indicf5", Representation.DEVANAGARI,
             question="Does preserving Urdu phonemes (nuqta) beat plain Devanagari?"),
}


# ── VRAM ─────────────────────────────────────────────────────────────────────


def _free_vram_mb() -> float | None:
    """
    Free VRAM across ALL processes, via the driver.

    `total - torch.cuda.memory_allocated()` is wrong and this project has been
    bitten by it: it sees only the current process and cheerfully reports 24 GB
    free while another process holds 20 GB. `mem_get_info` asks the driver.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return free / 1024 / 1024
    except Exception:
        return None


def _total_vram_mb() -> float | None:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return torch.cuda.mem_get_info()[1] / 1024 / 1024
    except Exception:
        return None


def _resolve_revision(repo: str) -> str | None:
    """Record the exact commit actually downloaded, for reproducibility."""
    try:
        from huggingface_hub import HfApi

        return HfApi().model_info(repo).sha
    except Exception:
        return None


# ── Model adapters ───────────────────────────────────────────────────────────
#
# Each returns a `synth(text) -> (audio_ndarray, sample_rate)` callable plus the
# resolved revision. Imports are inside the branch so a venv only ever imports
# its own stack.


def _load_voxcpm(reference: Path, lora_checkpoint: Path | None):
    from huggingface_hub import snapshot_download
    from voxcpm.core import VoxCPM

    kwargs: dict = {}
    if lora_checkpoint is not None:
        # Read the LoRA shape from the checkpoint's own lora_config.json rather
        # than hardcoding it. Getting it wrong does NOT raise — load_lora()
        # silently drops mismatched weights and yields a real-looking number for
        # the WRONG checkpoint. This exact bug was found and fixed once already
        # (see eval/run_voxcpm_lora_eval.py).
        cfg_path = lora_checkpoint / "lora_config.json"
        if not cfg_path.exists():
            raise SystemExit(f"{cfg_path} missing — cannot infer the LoRA shape safely.")
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))["lora_config"]
        from voxcpm.model.voxcpm2 import LoRAConfig

        keys = ("enable_lm", "enable_dit", "enable_proj", "r", "alpha", "dropout")
        kwargs["lora_config"] = LoRAConfig(**{k: raw[k] for k in keys})

    path = snapshot_download(repo_id=VOXCPM_REPO, revision=VOXCPM_REV)
    model = VoxCPM(voxcpm_model_path=path, enable_denoiser=False, optimize=False, **kwargs)
    if lora_checkpoint is not None:
        loaded, skipped = model.load_lora(str(lora_checkpoint))
        print(f"LoRA: loaded {len(loaded)} params, skipped {len(skipped)}", file=sys.stderr)
        if skipped:
            # Skipped weights mean the reconstructed adapter shape disagrees
            # with the checkpoint — the measurement would be of a model that is
            # only partly the thing we meant to test.
            raise SystemExit(f"refusing to measure: {len(skipped)} LoRA weights were skipped")

    sr = int(model.tts_model.sample_rate)

    def synth(text: str):
        audio = model.generate(
            text=text, reference_wav_path=str(reference),
            cfg_value=2.0, inference_timesteps=10, normalize=False,
        )
        return audio, sr

    return synth, VOXCPM_REV


def _load_indicf5(reference: Path, reference_text: str):
    import torch
    from transformers import AutoModel

    # IndicF5 REQUIRES a transcript of the reference clip — unlike VoxCPM2,
    # which is timbre-only. A blank ref_text is the documented trap: some F5
    # loaders silently run Whisper on the reference instead of erroring.
    if not reference_text.strip():
        raise SystemExit(
            "IndicF5 needs --reference-text (a transcript of the reference clip). "
            "Passing an empty one silently changes what is being measured."
        )
    model = AutoModel.from_pretrained(
        INDICF5_REPO, revision=INDICF5_REV, trust_remote_code=True,
    )
    if torch.cuda.is_available():
        model = model.to("cuda")

    def synth(text: str):
        audio = model(text, ref_audio_path=str(reference), ref_text=reference_text)
        return audio, 24000  # IndicF5 is fixed at 24 kHz

    return synth, INDICF5_REV


def _load_omnivoice(reference: Path, reference_text: str):
    """
    OmniVoice. Licence: Apache-2.0 CODE but **CC-BY-NC WEIGHTS** — permitted
    here for the owner's personal, API-key-gated use only, NOT commercially
    deployable. See docs/URDU_MODEL_LICENSING.md.
    """
    import torch
    from omnivoice import OmniVoice

    model = OmniVoice.from_pretrained(
        OMNIVOICE_REPO,
        device_map="cuda:0" if torch.cuda.is_available() else "cpu",
        dtype=torch.float16,
    )
    # Verified against the installed package rather than the model card:
    # generate(text, language=None, ref_text=None, ref_audio=None, ...)
    # -> list[np.ndarray].
    sr = int(getattr(model, "sample_rate", None) or getattr(model, "sr", None) or 24000)

    def synth(text: str):
        # Pass `language` EXPLICITLY. OmniVoice really does support Urdu (211 h
        # in its language table), and naming it removes the language-detection
        # step entirely — which is the exact failure suspected of VoxCPM2, where
        # Urdu is absent from the supported list and Perso-Arabic input may be
        # taken for Arabic. Leaving this to auto-detection would reintroduce
        # the variable this arm exists to eliminate.
        #
        # ref_text is optional (Whisper auto-transcribes when omitted) but
        # supplying it removes a source of run-to-run variance.
        kwargs = {"ref_text": reference_text} if reference_text.strip() else {}
        audio = model.generate(
            text=text, language="ur", ref_audio=str(reference), **kwargs
        )
        return (audio[0] if isinstance(audio, list) else audio), sr

    return synth, _resolve_revision(OMNIVOICE_REPO)


def _load_higgs(reference: Path, reference_text: str):
    """
    Higgs Audio v3 TTS.

    NOTE: this adapter is written from the model card and is the one API here
    NOT yet exercised against real weights. If it is wrong it must FAIL, not
    quietly produce something — so nothing is wrapped in a fallback. Verify on
    the pod and correct this function; do not "fix" it by catching the error.

    Licence: Boson Research & Non-Commercial. Permitted here for the owner's
    personal, API-key-gated use; NOT commercially deployable. See
    docs/URDU_MODEL_LICENSING.md.
    """
    import torch
    from transformers import AutoModel, AutoProcessor

    processor = AutoProcessor.from_pretrained(HIGGS_REPO, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        HIGGS_REPO, trust_remote_code=True,
        dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
    )
    if torch.cuda.is_available():
        model = model.to("cuda")

    def synth(text: str):
        inputs = processor(
            text=text, audio_prompt=str(reference),
            audio_prompt_transcript=reference_text or None, return_tensors="pt",
        )
        if torch.cuda.is_available():
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        out = model.generate(**inputs)
        audio = processor.batch_decode(out, output_audio=True)[0]
        return audio, 24000

    return synth, _resolve_revision(HIGGS_REPO)


_LOADERS = {
    "voxcpm2": _load_voxcpm,
    "indicf5": _load_indicf5,
    "omnivoice": _load_omnivoice,
    "higgs_v3": _load_higgs,
}


# ── Manifest ─────────────────────────────────────────────────────────────────


@dataclass
class ManifestRow:
    arm: str
    model: str
    representation: str
    lora: bool
    reference_id: str
    reference_path: str
    item_id: str
    category: str
    stresses: list[str]
    #: What the model was actually fed.
    input_text: str
    #: Canonical Urdu, the CER ground truth for EVERY arm regardless of input.
    cer_reference: str
    status: str = "ok"
    output: str = ""
    gen_time_sec: float | None = None
    duration_sec: float | None = None
    sample_rate: int | None = None
    error: str = ""


@dataclass
class RunMeta:
    arm: str
    model: str
    representation: str
    lora: bool
    reference_id: str
    hf_revision: str | None = None
    load_time_sec: float | None = None
    vram_total_mb: float | None = None
    vram_free_before_mb: float | None = None
    vram_free_after_load_mb: float | None = None
    vram_free_min_mb: float | None = None
    normalize: bool = False
    ascii_digits: bool = False
    gpu_name: str = ""
    python: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    status: str = "ok"
    error: str = ""

    @property
    def peak_vram_used_mb(self) -> float | None:
        if self.vram_total_mb is None or self.vram_free_min_mb is None:
            return None
        return self.vram_total_mb - self.vram_free_min_mb


def _gpu_name() -> str:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arm", required=True, choices=sorted(ARMS))
    p.add_argument("--reference", required=True, help="Reference speaker wav")
    p.add_argument("--reference-id", required=True, help="e.g. 'male' / 'female'")
    p.add_argument("--reference-text", default="",
                   help="Transcript of the reference. REQUIRED by IndicF5 and Higgs.")
    p.add_argument("--lora-checkpoint", default=None, help="Arm D only")
    p.add_argument("--out-dir", default=str(OUT_DIR))
    p.add_argument("--normalize", action="store_true", help="Ladder rung B")
    p.add_argument("--ascii-digits", action="store_true",
                   help="Fold Eastern Arabic-Indic digits. OFF by default: may hurt.")
    p.add_argument("--limit", type=int, default=None, help="First N items (smoke test)")
    args = p.parse_args()

    arm = ARMS[args.arm]
    reference = Path(args.reference)
    if not reference.exists():
        raise SystemExit(f"reference not found: {reference}")

    out_dir = Path(args.out_dir) / f"arm_{arm.id}_{args.reference_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    corpus = load_corpus()
    by_id = {i.id: i for i in corpus}
    texts = render(corpus, arm.representation,
                   normalize=args.normalize, ascii_digits=args.ascii_digits)
    if args.limit:
        texts = texts[: args.limit]

    meta = RunMeta(
        arm=arm.id, model=arm.model, representation=arm.representation, lora=arm.lora,
        reference_id=args.reference_id, vram_total_mb=_total_vram_mb(),
        vram_free_before_mb=_free_vram_mb(), normalize=args.normalize,
        ascii_digits=args.ascii_digits, gpu_name=_gpu_name(),
    )
    rows: list[ManifestRow] = []

    def write_manifest() -> None:
        payload = {
            "meta": {**asdict(meta), "peak_vram_used_mb": meta.peak_vram_used_mb},
            "arm_question": arm.question,
            "rows": [asdict(r) for r in rows],
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # Load. A load failure is a RECORDED result — "could not run, and why" — not
    # a silently absent arm.
    print(f"[{arm.id}] loading {arm.model} ({arm.representation})...", file=sys.stderr)
    t0 = time.time()
    try:
        loader = _LOADERS[arm.model]
        if arm.model == "voxcpm2":
            ckpt = Path(args.lora_checkpoint) if args.lora_checkpoint else None
            if arm.lora and ckpt is None:
                raise SystemExit("arm D requires --lora-checkpoint")
            synth, revision = loader(reference, ckpt)
        else:
            synth, revision = loader(reference, args.reference_text)
    except BaseException as exc:  # SystemExit included: it is still a result
        meta.status, meta.error = "could_not_run", f"{type(exc).__name__}: {exc}"
        meta.load_time_sec = time.time() - t0
        traceback.print_exc()
        for item_id, text in texts:
            item = by_id[item_id]
            rows.append(ManifestRow(
                arm=arm.id, model=arm.model, representation=arm.representation,
                lora=arm.lora, reference_id=args.reference_id,
                reference_path=str(reference), item_id=item_id, category=item.category,
                stresses=list(item.stresses), input_text=text,
                cer_reference=item.cer_reference, status="could_not_run",
                error=meta.error,
            ))
        write_manifest()
        print(f"\n[{arm.id}] COULD NOT RUN: {meta.error}", file=sys.stderr)
        print(f"Recorded in {out_dir / 'manifest.json'}", file=sys.stderr)
        return 0  # recorded, not a harness failure

    meta.load_time_sec = time.time() - t0
    meta.hf_revision = revision
    meta.vram_free_after_load_mb = _free_vram_mb()
    meta.vram_free_min_mb = meta.vram_free_after_load_mb
    print(f"[{arm.id}] loaded in {meta.load_time_sec:.1f}s (rev {revision})", file=sys.stderr)

    import soundfile as sf

    for item_id, text in texts:
        item = by_id[item_id]
        row = ManifestRow(
            arm=arm.id, model=arm.model, representation=arm.representation,
            lora=arm.lora, reference_id=args.reference_id,
            reference_path=str(reference), item_id=item_id, category=item.category,
            stresses=list(item.stresses), input_text=text,
            cer_reference=item.cer_reference,
        )
        try:
            t1 = time.time()
            audio, sr = synth(text)
            gen = time.time() - t1
            path = out_dir / f"{arm.id}_{args.reference_id}_{item_id}.wav"
            sf.write(str(path), audio, sr)
            # Repo-relative when possible so the manifest stays portable between
            # the pod and the local machine; absolute otherwise, because
            # `--out-dir` may legitimately point outside the repo (a scratch dir
            # on the pod volume) and `relative_to` RAISES rather than falling
            # back. The consumers resolve either form.
            try:
                row.output = str(path.relative_to(_REPO_ROOT))
            except ValueError:
                row.output = str(path)
            row.gen_time_sec = gen
            row.sample_rate = sr
            row.duration_sec = float(sf.info(str(path)).duration)
            free = _free_vram_mb()
            if free is not None and meta.vram_free_min_mb is not None:
                meta.vram_free_min_mb = min(meta.vram_free_min_mb, free)
            rtf = gen / row.duration_sec if row.duration_sec else float("nan")
            print(f"  [{item_id}] {row.duration_sec:.2f}s in {gen:.2f}s (RTF {rtf:.3f})",
                  file=sys.stderr)
        except Exception as exc:
            row.status, row.error = "failed", f"{type(exc).__name__}: {exc}"
            print(f"  [{item_id}] FAILED: {row.error}", file=sys.stderr)
            traceback.print_exc()
        rows.append(row)
        write_manifest()  # after every item: a crash keeps what already ran

    ok = sum(1 for r in rows if r.status == "ok")
    print(f"\n[{arm.id}] {ok}/{len(rows)} generated -> {out_dir}", file=sys.stderr)
    if meta.peak_vram_used_mb:
        print(f"[{arm.id}] peak VRAM ~{meta.peak_vram_used_mb:.0f} MB "
              f"of {meta.vram_total_mb:.0f} MB", file=sys.stderr)
    print("Next: score in .venv-eval with eval/score_urdu_bakeoff.py", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
