"""
Phase A0 -- the cheapest test that could moot the whole Roman-Urdu conversion
feature: does OmniVoice already handle Latin-script Roman Urdu directly?

OMNIVOICE_URDU only *declares* (ur, ARABIC). Nobody has ever fed it Latin. But
VoxCPM2 renders Roman Urdu fine, and OmniVoice saw 211h of Urdu -- so it is
entirely possible the model reads "main office ja raha hoon" correctly on its
own. If it does, the honest fix is a catalog cell, not an LLM pipeline.

This is a RAW MODEL CAPABILITY probe, so it deliberately does NOT call
resolve(): routing would (correctly) refuse a (ur, LATIN) request for a spec
that declares (ur, ARABIC), and that refusal is the thing under question. It
calls OmniVoiceBackend directly and feeds text verbatim.

Each corpus item is synthesized TWICE against the same reference with the same
loaded checkpoint -- once from `roman`, once from `perso_arabic`. The gold arm
is the control: it is the ceiling any conversion pipeline could ever reach, so
the owner is judging "is Roman-direct close enough to the ceiling to skip the
pipeline", not "is Roman-direct good in the abstract".

Items are chosen to contain no bare digits, so number normalization (which
production applies and this script does not) cannot confound the comparison.

Run on the pod:
    backend/.venv-omnivoice/bin/python eval/run_a0_roman_direct.py
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.inference.runtimes.omnivoice import OmniVoiceBackend  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS = _REPO_ROOT / "eval" / "fixtures" / "urdu_corpus.json"
OUT_DIR = _REPO_ROOT / "eval" / "results" / "a0_roman_direct"

_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

# No bare digits in any of these -- see module docstring.
_ITEM_IDS = (
    "owner_01_sick",
    "owner_02_file",
    "owner_03_deadline",
    "owner_04_late",
    "owner_05_github",
    "colloquial",
    "technical",
    "long_multiclause",
)

_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"


def _load_items() -> list[dict]:
    corpus = json.loads(_CORPUS.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in corpus["items"]}
    missing = [i for i in _ITEM_IDS if i not in by_id]
    if missing:
        raise SystemExit(f"corpus is missing items: {missing}")
    return [by_id[i] for i in _ITEM_IDS]


def _write_page(items: list[dict]) -> Path:
    rows = []
    for item in items:
        rows.append(
            f"""
  <div class="item">
    <div class="meta"><b>{escape(item["id"])}</b> &mdash; {escape(item["category"])}
      &mdash; stresses: {escape(", ".join(item["stresses"]))}</div>
    <div class="pair">
      <div class="col">
        <div class="label">A &mdash; Roman Urdu fed DIRECTLY (the question)</div>
        <div class="text" dir="ltr">{escape(item["roman"])}</div>
        <audio controls src="{escape(item["id"])}_roman.wav"></audio>
      </div>
      <div class="col">
        <div class="label">B &mdash; Perso-Arabic gold (the ceiling / control)</div>
        <div class="text" dir="rtl">{escape(item["perso_arabic"])}</div>
        <audio controls src="{escape(item["id"])}_arabic.wav"></audio>
      </div>
    </div>
  </div>"""
        )

    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>A0 &mdash; OmniVoice on Roman Urdu, direct</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 2rem auto;
       padding: 0 1rem; background:#0b0d12; color:#e6e8ee; }}
h1 {{ font-size: 1.3rem; }}
.item {{ margin: 1.5rem 0; padding: 1rem; background: #161922; border-radius: 8px; }}
.meta {{ font-size: .9rem; color: #9aa4b2; margin-bottom: .6rem; }}
.pair {{ display: flex; gap: 1rem; }}
.col {{ flex: 1; }}
.label {{ font-size: .75rem; color: #6c8cff; text-transform: uppercase;
         letter-spacing: .04em; margin-bottom: .3rem; }}
.text {{ font-size: 1.1rem; margin: .4rem 0; min-height: 3.4em; }}
audio {{ width: 100%; margin-top: .3rem; }}
.q {{ background:#1d2230; border-left:3px solid #6c8cff; padding:.8rem 1rem;
     border-radius:4px; }}
</style></head>
<body>
<h1>A0 &mdash; does OmniVoice read Roman Urdu on its own?</h1>
<p class="q"><b>The only question:</b> does column A pronounce the sentence correctly in Urdu?
If it does &mdash; even a bit worse than B &mdash; the Roman&rarr;Urdu LLM pipeline is
unnecessary and the honest fix is a catalog cell declaring <code>(ur, LATIN)</code> on
<code>omnivoice_urdu</code>. If column A is gibberish, English-accented, or silent, the
pipeline is justified and Phase A continues.</p>
<p>Column B is the same sentence in Perso-Arabic &mdash; the <b>ceiling</b>, i.e. the best any
conversion pipeline could ever deliver. Same reference voice, same loaded checkpoint, same
sampling. Text is fed <b>verbatim</b>: no number normalization anywhere, and these items
contain no bare digits, so nothing is confounded.</p>
{"".join(rows)}
</body></html>
"""
    out = OUT_DIR / "listen.html"
    out.write_text(page, encoding="utf-8")
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    items = _load_items()

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", _HF_REPO, _HF_REVISION)
    print("Loaded.", file=sys.stderr)

    manifest = []
    try:
        for item in items:
            for arm, text in (("roman", item["roman"]), ("arabic", item["perso_arabic"])):
                out_path = OUT_DIR / f"{item['id']}_{arm}.wav"
                print(f"synth {item['id']} [{arm}] ...", file=sys.stderr)
                backend.synth(
                    text=text,
                    reference_audio=str(_REF_AUDIO),
                    output_path=str(out_path),
                    params={"language": "ur"},
                    sample_rate=24000,
                    reference_text=_REF_TEXT,
                )
                manifest.append(
                    {
                        "id": item["id"],
                        "arm": arm,
                        "text": text,
                        "wav": out_path.name,
                        "bytes": out_path.stat().st_size,
                    }
                )
    finally:
        backend.unload()

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "purpose": "Phase A0 -- OmniVoice fed Roman Urdu directly vs Perso-Arabic gold.",
                "reference_audio": _REF_AUDIO.name,
                "hf_revision": _HF_REVISION,
                "note": "Raw backend probe; resolve() deliberately not called. Verbatim text, no normalization.",
                "clips": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    page = _write_page(items)
    print(f"wrote {len(manifest)} clips + {page}", file=sys.stderr)


if __name__ == "__main__":
    main()
