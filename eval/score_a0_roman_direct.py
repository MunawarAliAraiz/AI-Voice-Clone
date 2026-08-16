"""
Screen the A0 clips so the owner's listening pass starts with a signal rather
than 16 blind clips.

Both arms are scored against the SAME `perso_arabic` gold, per the corpus's
`cer_reference_rule`: whatever representation went in, the audio is supposed to
be Urdu speech, so the one canonical Urdu string is the only defensible ground
truth. That is exactly what makes the two arms comparable here.

This is a SCREEN, not the verdict (the corpus's `asr_caveat` applies: Whisper's
Urdu is weak and its script choice is unstable). It answers one coarse question
-- is the Roman arm in the same league as the gold arm, or is it broken? A big
gap means OmniVoice is not reading Latin as Urdu and the conversion pipeline is
justified. A small gap sends the decision to the ears.

Run on the pod:
    backend/.venv-eval/bin/python eval/score_a0_roman_direct.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_harness import EvalHarness, compute_cer  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS = _REPO_ROOT / "eval" / "fixtures" / "urdu_corpus.json"
_CLIPS = _REPO_ROOT / "eval" / "results" / "a0_roman_direct"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"


def main() -> None:
    manifest = json.loads((_CLIPS / "manifest.json").read_text(encoding="utf-8"))
    corpus = json.loads(_CORPUS.read_text(encoding="utf-8"))
    gold = {item["id"]: item["perso_arabic"] for item in corpus["items"]}

    harness = EvalHarness(device="cuda")
    rows = []
    for clip in manifest["clips"]:
        wav = _CLIPS / clip["wav"]
        target = gold[clip["id"]]
        transcript = harness.transcribe(wav, "ur")
        rows.append(
            {
                "id": clip["id"],
                "arm": clip["arm"],
                "input_text": clip["text"],
                "gold_perso_arabic": target,
                "whisper_transcript": transcript,
                "cer_vs_gold": round(compute_cer(target, transcript, "ur"), 4),
                "speaker_cosine": round(harness.speaker_similarity(_REF_AUDIO, wav), 4),
            }
        )
        r = rows[-1]
        print(
            f"{r['id']:<20} {r['arm']:<7} CER={r['cer_vs_gold']:.4f} "
            f"cos={r['speaker_cosine']:.4f}",
            file=sys.stderr,
        )

    def mean(arm: str, key: str) -> float:
        vals = [r[key] for r in rows if r["arm"] == arm]
        return round(sum(vals) / len(vals), 4)

    summary = {
        "note": "SCREEN ONLY -- Whisper's Urdu is weak (see corpus asr_caveat). "
                "The A0 verdict is the owner's listening pass, not these numbers.",
        "roman_mean_cer": mean("roman", "cer_vs_gold"),
        "arabic_mean_cer": mean("arabic", "cer_vs_gold"),
        "roman_mean_cosine": mean("roman", "speaker_cosine"),
        "arabic_mean_cosine": mean("arabic", "speaker_cosine"),
    }
    summary["cer_gap_roman_minus_arabic"] = round(
        summary["roman_mean_cer"] - summary["arabic_mean_cer"], 4
    )

    out = _CLIPS / "scores.json"
    out.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2), file=sys.stderr)
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
