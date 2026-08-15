"""
Urdu bake-off — SCORING pass. Runs in `.venv-eval`, after synthesis.

Reads every `arm_*/manifest.json` produced by eval/run_urdu_bakeoff.py, scores
each generated clip, and writes `scores.json` per arm plus a combined
`summary.json`.

    .venv-eval/bin/python eval/score_urdu_bakeoff.py

METHODOLOGY, and its limits
---------------------------
CER is scored against `cer_reference` — the canonical Perso-Arabic Urdu — for
EVERY arm, whatever script that arm was fed, and Whisper is always asked for
Urdu. The audio is Urdu speech in all arms, so that is the only ground truth
that keeps arms comparable; scoring arm C against the Devanagari it happened to
receive would silently measure a different thing than arm B.

These numbers are a SCREEN, not a ranking:

  * Whisper's Urdu is weak and its script choice for Urdu/Hindi audio is
    unstable, so CER here detects gross breakage — silence, wrong language,
    garbage — and little else.
  * ECAPA-TDNN is English-trained and out-of-distribution for this voice. That
    is not a hypothetical: VoxCPM2 once passed CER and nearly passed cosine and
    still sounded like a stranger to the owner.

The decision comes from the blind listening scores. Nothing here may set
`LanguageSupport.verified`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_harness import EvalHarness, compute_cer  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff"

#: Whisper is always asked for Urdu — see the module docstring.
CER_LANGUAGE = "ur"


def _score_arm(harness: EvalHarness, manifest_path: Path) -> dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    meta, rows = payload["meta"], payload["rows"]
    arm = meta["arm"]

    if meta.get("status") == "could_not_run":
        # Carried through verbatim so the results table can show WHY rather
        # than leaving a blank that reads as "tested, fine".
        print(f"[{arm}] could_not_run: {meta.get('error', '')}", file=sys.stderr)
        return {"meta": meta, "rows": rows, "aggregate": None,
                "status": "could_not_run", "error": meta.get("error", "")}

    scored: list[dict] = []
    for row in rows:
        out = dict(row)
        if row["status"] != "ok" or not row["output"]:
            scored.append(out)
            continue
        wav = _REPO_ROOT / row["output"]
        if not wav.exists():
            out["status"] = "missing_audio"
            out["error"] = f"{wav} not found"
            scored.append(out)
            continue
        try:
            transcript = harness.transcribe(wav, CER_LANGUAGE)
            out["whisper_transcript"] = transcript
            out["cer"] = compute_cer(row["cer_reference"], transcript, CER_LANGUAGE)
            out["speaker_sim"] = harness.speaker_similarity(
                _REPO_ROOT / row["reference_path"]
                if not Path(row["reference_path"]).is_absolute()
                else row["reference_path"],
                wav,
            )
            if row.get("gen_time_sec") and row.get("duration_sec"):
                out["rtf"] = row["gen_time_sec"] / row["duration_sec"]
            print(f"  [{arm}/{row['item_id']}] CER {out['cer']:.4f}  "
                  f"cos {out['speaker_sim']:.4f}", file=sys.stderr)
        except Exception as exc:
            out["status"] = "score_failed"
            out["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  [{arm}/{row['item_id']}] SCORE FAILED: {out['error']}", file=sys.stderr)
            traceback.print_exc()
        scored.append(out)

    ok = [r for r in scored if r.get("cer") is not None]

    def med(key: str) -> float | None:
        vals = [r[key] for r in ok if r.get(key) is not None]
        return statistics.median(vals) if vals else None

    # Median, not mean: n is small (13 items) and one garbled clip can push a
    # mean CER past 1.0 and make an otherwise-fine arm look broken.
    aggregate = {
        "n_scored": len(ok),
        "n_rows": len(scored),
        "cer_median": med("cer"),
        "speaker_sim_median": med("speaker_sim"),
        "rtf_median": med("rtf"),
        "peak_vram_used_mb": meta.get("peak_vram_used_mb"),
        "load_time_sec": meta.get("load_time_sec"),
    }

    # The code-switching slice — the plan's "arm G". A reporting view over items
    # already generated, not a separate synthesis run.
    cs = [r for r in ok if "code_switch" in (r.get("stresses") or [])]
    if cs:
        aggregate["code_switch"] = {
            "n": len(cs),
            "cer_median": statistics.median([r["cer"] for r in cs]),
            "speaker_sim_median": statistics.median([r["speaker_sim"] for r in cs]),
        }

    return {"meta": meta, "rows": scored, "aggregate": aggregate, "status": "ok"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results-dir", default=str(RESULTS))
    p.add_argument("--device", default="cuda")
    args = p.parse_args()

    root = Path(args.results_dir)
    manifests = sorted(root.glob("arm_*/manifest.json"))
    if not manifests:
        raise SystemExit(f"no arm_*/manifest.json under {root} — run the synthesis pass first")

    harness = EvalHarness(device=args.device)
    summary: list[dict] = []

    for mpath in manifests:
        print(f"\n== {mpath.parent.name} ==", file=sys.stderr)
        result = _score_arm(harness, mpath)
        (mpath.parent / "scores.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        summary.append({
            "arm": result["meta"]["arm"],
            "model": result["meta"]["model"],
            "representation": result["meta"]["representation"],
            "lora": result["meta"].get("lora", False),
            "reference_id": result["meta"]["reference_id"],
            "status": result["status"],
            "error": result.get("error", ""),
            **(result["aggregate"] or {}),
        })

    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n" + "=" * 78, file=sys.stderr)
    print(f"{'arm':<4} {'model':<10} {'repr':<18} {'ref':<7} "
          f"{'CER':>8} {'cos':>8} {'RTF':>7}", file=sys.stderr)
    print("-" * 78, file=sys.stderr)
    for s in summary:
        if s["status"] != "ok":
            print(f"{s['arm']:<4} {s['model']:<10} {s['representation']:<18} "
                  f"{s['reference_id']:<7}  COULD NOT RUN", file=sys.stderr)
            continue
        f = lambda v, w=8, p=4: (f"{v:>{w}.{p}f}" if v is not None else " " * (w - 1) + "-")  # noqa: E731
        print(f"{s['arm']:<4} {s['model']:<10} {s['representation']:<18} "
              f"{s['reference_id']:<7} {f(s['cer_median'])} {f(s['speaker_sim_median'])} "
              f"{f(s['rtf_median'], 7, 3)}", file=sys.stderr)
    print("=" * 78, file=sys.stderr)
    print("\nSCREEN ONLY. The decision is the blind listening scores — build the", file=sys.stderr)
    print("page with eval/build_listen_page.py. Do not set verified=True from this.",
          file=sys.stderr)
    print(f"\nWrote {root / 'summary.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
