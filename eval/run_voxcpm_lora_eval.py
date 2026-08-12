"""
VoxCPM2 LoRA POC — generate baseline (zero-shot, no LoRA) and LoRA-fine-tuned
audio from the SAME reference speaker and the SAME standard target sentence
already used for every other before/after comparison in this project
(eval/fixtures/README.md, docs/PHASE_A_RESULTS.md, docs/PHASE4_CHATTERBOX_DESIGN.md).

Loads the model the same way production does (`voxcpm.core.VoxCPM`,
`reference_wav_path`-only cloning, `cfg_value`/`inference_timesteps` defaults —
see backend/app/inference/runtimes/voxcpm.py) so the "before" number is
directly comparable to every other VoxCPM2 result on record, and the "after"
number isolates exactly the LoRA delta: same reference, same text, same
params, only the LoRA weights differ.

Trick used to get both numbers from ONE model load instead of two: VoxCPM2's
LoRALinear initializes lora_B to all zeros (voxcpm/modules/layers/lora.py),
so a model constructed WITH a lora_config but BEFORE any trained weights are
loaded is mathematically identical to the base model (the LoRA delta is
exactly zero). So: construct once with the same lora_config used for
training, generate the baseline first, THEN call model.load_lora(ckpt) and
generate again — one load, two directly comparable samples, same process.

Runs under backend/.venv-voxcpm on the pod (not the API's own venv, which
must stay torch-free per CLAUDE.md rule 2).

Usage (on the pod):
    /workspace/AI-Voice-Clone/backend/.venv-voxcpm/bin/python \
        eval/run_voxcpm_lora_eval.py \
        --lora-checkpoint /workspace/engines-lab/voxcpm-lora/checkpoints/poc_lora/latest
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

MODEL_ID = "voxcpm2"
HF_REPO = "openbmb/VoxCPM2"
HF_REVISION = "bffb3df5a29440629464e5e839f4d214c8714c3d"

# Same reference and same standard target sentence as every other VoxCPM2 /
# Chatterbox comparison in this project (eval/fixtures/README.md).
_REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
TARGET_TEXT_UR = (
    "اگر تمہیں فارغ وقت ملے تو مجھے فون کر لینا، ہم کہیں باہر کھانا کھانے چلتے ہیں "
    "اور تھوڑی دیر گپ شپ بھی کر لیں گے۔"
)
TARGET_TEXT_HI = (
    "अगर तुम्हें फ़ारिग़ वक़्त मिले तो मुझे फ़ोन कर लेना, हम कहीं बाहर खाना खाने चलते हैं "
    "और थोड़ी देर गप शप भी कर लेंगे।"
)

OUT_DIR = _REPO_ROOT / "eval" / "results" / "voxcpm_lora"

# Must match the `lora:` block in eval/voxcpm_lora_poc.yaml exactly, or
# load_lora() will load weights into a differently-shaped adapter.
LORA_KWARGS = dict(enable_lm=True, enable_dit=True, enable_proj=False, r=32, alpha=32, dropout=0.0)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lora-checkpoint", required=True, help="Path to the trained LoRA checkpoint's `latest/` folder")
    p.add_argument("--out-dir", default=str(OUT_DIR))
    args = p.parse_args()

    if not REFERENCE_AUDIO.exists():
        raise SystemExit(f"reference audio not found: {REFERENCE_AUDIO}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import snapshot_download
    from voxcpm.core import VoxCPM
    from voxcpm.model.voxcpm2 import LoRAConfig

    model_path = snapshot_download(repo_id=HF_REPO, revision=HF_REVISION)
    print(f"Loading {MODEL_ID} from {model_path} with lora_config={LORA_KWARGS}...", file=sys.stderr)

    t0 = time.time()
    model = VoxCPM(
        voxcpm_model_path=model_path,
        enable_denoiser=False,
        optimize=False,
        lora_config=LoRAConfig(**LORA_KWARGS),
    )
    print(f"Loaded in {time.time() - t0:.2f}s", file=sys.stderr)

    manifest: list[dict] = []

    def synth(tag: str, text: str, language: str) -> None:
        out_path = out_dir / f"voxcpm_lora_{tag}.wav"
        t0 = time.time()
        audio = model.generate(
            text=text,
            reference_wav_path=str(REFERENCE_AUDIO),
            cfg_value=2.0,
            inference_timesteps=10,
            normalize=False,
        )
        gen_sec = time.time() - t0
        import soundfile as sf

        sr = int(model.tts_model.sample_rate)
        sf.write(str(out_path), audio, sr)
        duration = len(audio) / sr
        print(f"[{tag}] wrote {out_path} — {duration:.2f}s audio in {gen_sec:.2f}s (RTF {gen_sec/duration:.3f})", file=sys.stderr)
        manifest.append(
            {
                "tag": tag,
                "language": language,
                "text": text,
                "output": str(out_path),
                "gen_time_sec": gen_sec,
                "duration_sec": duration,
                "sample_rate": sr,
            }
        )

    # 1. Baseline — lora_B is zero-initialized, so this is mathematically the
    #    base model with no LoRA contribution at all.
    synth("baseline_ur", TARGET_TEXT_UR, "ur")
    synth("baseline_hi", TARGET_TEXT_HI, "hi")

    # 2. Load the trained LoRA delta, generate the same two samples again.
    loaded, skipped = model.load_lora(args.lora_checkpoint)
    print(f"Loaded {len(loaded)} LoRA parameters from {args.lora_checkpoint}, skipped {len(skipped)}", file=sys.stderr)
    synth("lora_ur", TARGET_TEXT_UR, "ur")
    synth("lora_hi", TARGET_TEXT_HI, "hi")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nWrote {manifest_path}", file=sys.stderr)
    print(
        "\nNext: score each output against the reference with eval_harness.py, e.g.:\n"
        f"  python eval/eval_harness.py --reference {REFERENCE_AUDIO} "
        f"--generated {out_dir}/voxcpm_lora_baseline_ur.wav --text '<ur text>' --language ur",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
