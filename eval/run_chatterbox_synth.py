"""
Phase 4c — generate Chatterbox audio for the Phase A eval harness to score.

Runs under `.venv-chatterbox` (needs torch + chatterbox-tts installed there,
per `scripts/pod-bootstrap.sh`'s Chatterbox section) — NOT the API's own venv,
which must stay torch-free per CLAUDE.md rule 2.

Uses the exact `hf_repo`/`hf_revision`/`model_id` pinned in
`backend/app/inference/catalog.py`'s `CHATTERBOX_ML_V3` (rule 7: pinned
revision, not `from_pretrained()`'s hardcoded `main`), and the same reference
speaker clip + target sentence corpus `docs/PHASE_A_RESULTS.md` already used
for VoxCPM/F5, so the results land in the same comparison table.

Deliberately uses Chatterbox's *default* params (exaggeration=0.5,
cfg_weight=0.5) — this validates the model itself, independent of Speech
Direction's blend mapping (`app/jobs/direction.py`), same as how VoxCPM/F5
were Phase-A-tested with plain defaults before any direction mapping existed.

Usage:
    <repo>/backend/.venv-chatterbox/bin/python eval/run_chatterbox_synth.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.inference.runtimes.chatterbox import ChatterboxBackend  # noqa: E402

MODEL_ID = "chatterbox_ml_v3"
HF_REPO = "ResembleAI/chatterbox"
HF_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"

REFERENCE_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
OUT_DIR = _REPO_ROOT / "eval" / "results" / "chatterbox"


@dataclass(frozen=True)
class Case:
    language_id: str  # what Chatterbox's `generate(language_id=...)` expects
    eval_language: str  # eval_harness.py's --language choice (ur/hi/en)
    text: str
    out_name: str


# Hindi text is the exact Devanagari standard target sentence from
# eval/fixtures/README.md, already used for F5 and VoxCPM2 in
# docs/PHASE_A_RESULTS.md — keeping it identical makes this row directly
# comparable to those. English is a plain translation of the same content
# (Chatterbox's SUPPORTED_LANGUAGES has no 'ur', so Urdu cannot be tested
# directly — see docs/PHASE4_CHATTERBOX_DESIGN.md).
CASES = (
    Case(
        language_id="hi",
        eval_language="hi",
        text=(
            "अगर तुम्हें फ़ारिग़ वक़्त मिले तो मुझे फ़ोन कर लेना, "
            "हम कहीं बाहर खाना खाने चलते हैं और थोड़ी देर गप शप भी कर लेंगे।"
        ),
        out_name="chatterbox_hi.wav",
    ),
    Case(
        language_id="en",
        eval_language="en",
        text=(
            "If you get some free time, give me a call and we'll go out to "
            "eat and chat for a while."
        ),
        out_name="chatterbox_en.wav",
    ),
)


def main() -> int:
    if not REFERENCE_AUDIO.exists():
        raise SystemExit(f"reference audio not found: {REFERENCE_AUDIO}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    backend = ChatterboxBackend()

    print(f"Loading {MODEL_ID} from {HF_REPO}@{HF_REVISION[:12]}...")
    load_sec = backend.load(MODEL_ID, HF_REPO, HF_REVISION)
    print(f"Loaded in {load_sec:.2f}s")

    manifest: list[dict] = []
    for case in CASES:
        out_path = OUT_DIR / case.out_name
        t0 = time.time()
        result = backend.synth(
            text=case.text,
            reference_audio=str(REFERENCE_AUDIO),
            output_path=str(out_path),
            params={
                "language_id": case.language_id,
                "exaggeration": 0.5,
                "cfg_weight": 0.5,
            },
            sample_rate=24000,
        )
        wall_sec = time.time() - t0
        print(f"[{case.language_id}] wrote {out_path} — {result}")
        manifest.append(
            {
                "language_id": case.language_id,
                "eval_language": case.eval_language,
                "text": case.text,
                "output": str(out_path),
                "gen_time_sec": result["gen_time_sec"],
                "wall_sec": wall_sec,
                "duration_sec": result["duration_sec"],
                "sample_rate": result["sample_rate"],
            }
        )

    backend.unload()

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nWrote {manifest_path}")
    print(
        "\nNext: score each output with eval/eval_harness.py under a venv that "
        "has torch/transformers/speechbrain/jiwer (e.g. .venv-eval), e.g.:\n"
        f"  python eval/eval_harness.py --reference {REFERENCE_AUDIO} "
        f"--generated {OUT_DIR}/chatterbox_hi.wav --text '<hi text>' "
        "--language hi --gen-time-sec <from manifest.json>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
