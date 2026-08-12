"""
VoxCPM2 LoRA POC — build the JSONL manifests `scripts/train_voxcpm_finetune.py`
(OpenBMB/VoxCPM `voxcpm.training.load_audio_text_datasets`) expects.

Reads `eval/training/transcripts_review.tsv` (the human-reviewed file — see
`eval/training/README` context in the handoff docs: `perso_arabic_ur` is the
verified ground-truth transcript in real Urdu script, `devanagari_hi` is an
uncorrected Whisper ASR pass). Uses `perso_arabic_ur` as the training text:
a tokenizer probe against the pinned VoxCPM2 checkpoint
(`openbmb/VoxCPM2@bffb3df5a29440629464e5e839f4d214c8714c3d`) found ZERO
unknown-token fallbacks on Perso-Arabic Urdu, and it tokenizes MORE
efficiently than the Devanagari ASR pass (1.46 vs 1.81 tokens/char) — the
human-reviewed transcript is both more accurate and no harder for the model
to consume, so there is no reason to prefer the lossier ASR column here.

Output manifests reference the dataset as uploaded to the pod under
`/workspace/engines-lab/voxcpm-lora/dataset/training/wav/` (scp'd there
directly — `eval/training/` is untracked in git per the project's
biometric-data consent constraint, so the pod does not get it via `git
pull`). Adjust `--audio-root` if the dataset lives elsewhere.

Split: clips 33-36 held out as a tiny validation set (audio previews +
val loss during training), clips 01-32 for training — in-range for the
project's own documented VoxCPM2 LoRA guidance of "5-50 clips" for
single-speaker identity cloning.

Usage (no GPU required, pure text processing):
    python eval/prepare_voxcpm_lora_manifest.py \
        --tsv eval/training/transcripts_review.tsv \
        --audio-root /workspace/engines-lab/voxcpm-lora/dataset/training/wav \
        --out-dir eval/training
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

VAL_CLIPS = {"clip_33.wav", "clip_34.wav", "clip_35.wav", "clip_36.wav"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tsv", default="eval/training/transcripts_review.tsv")
    p.add_argument(
        "--audio-root",
        default="/workspace/engines-lab/voxcpm-lora/dataset/training/wav",
        help="Directory the *pod* will read wav files from (written into the manifest paths).",
    )
    p.add_argument("--out-dir", default="eval/training")
    args = p.parse_args()

    tsv_path = Path(args.tsv)
    rows = list(csv.DictReader(tsv_path.open("r", encoding="utf-8"), delimiter="\t"))

    train_rows, val_rows = [], []
    skipped = []
    for row in rows:
        clip = row["clip"].strip()
        text = row["perso_arabic_ur"].strip()
        if not text:
            skipped.append(clip)
            continue
        entry = {"audio": f"{args.audio_root}/{clip}", "text": text}
        (val_rows if clip in VAL_CLIPS else train_rows).append(entry)

    out_dir = Path(args.out_dir)
    train_path = out_dir / "manifest_lora_train.jsonl"
    val_path = out_dir / "manifest_lora_val.jsonl"

    with train_path.open("w", encoding="utf-8") as f:
        for entry in train_rows:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    with val_path.open("w", encoding="utf-8") as f:
        for entry in val_rows:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"train: {len(train_rows)} clips -> {train_path}", file=sys.stderr)
    print(f"val:   {len(val_rows)} clips -> {val_path}", file=sys.stderr)
    if skipped:
        print(f"skipped (empty transcript): {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
