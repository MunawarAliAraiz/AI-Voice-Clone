"""
Follow-up to the owner's A0 listen (2026-08-16), which reported that column B
-- the Perso-Arabic gold, i.e. the supposed ceiling -- still mispronounces
"late" and "database".

That report splits into two different things, and only one of them is a real
production defect:

1. `database` -- NOT a production defect, a defect in how A0 was run. A0 fed
   text VERBATIM to keep number normalization from confounding the Roman-vs-gold
   comparison, which also skipped `domain/urdu_text.py`'s loanword lexicon.
   Production would have rewritten `database` to `ڈیٹا base`, the mixed
   respelling that docs/URDU_BAKEOFF_RESULTS.md §5c verified fixes this exact
   sentence. So the owner heard raw `database`, which production never sends.
   This script proves that by synthesizing both and letting the owner compare.

2. `late` -- a REAL and NEW finding. It is not in `_LOANWORD_LEXICON`, and
   URDU_BAKEOFF_RESULTS.md §5b explicitly recorded it as fine ("code-switched
   'office'/'late' both landed", owner_04_late ✅). A previously-passing word
   failing on re-listen is exactly why that document says a pass is not
   trustworthy without a re-listen. Candidate respellings are synthesized here
   so the fix gets the same verify-by-ear discipline URL and database got --
   the lexicon's docstring requires it, and no word goes in on a hunch.

`office` is included as a control: it is the code-switched word §5b passed and
the owner did NOT complain about, so if it also sounds wrong here the problem
is broader than a per-word lexicon can fix.

Run on the pod:
    backend/.venv-omnivoice/bin/python eval/run_loanword_late_check.py
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "loanword_late_check"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)
_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"

# (group, variant_id, label, text)
_CASES: list[tuple[str, str, str, str]] = [
    # -- "late": the real finding. Full sentence context, since URDU_BAKEOFF
    # RESULTS §5c found these words behave differently bare vs in a busy
    # sentence, and the busy realistic sentence is the case that matters.
    ("late", "latin", "Latin `late` (what runs today)",
     "یار، آج office میں کافی کام تھا، اس لیے میں late ہو گیا۔"),
    ("late", "urdu_let", "Respelled لیٹ",
     "یار، آج office میں کافی کام تھا، اس لیے میں لیٹ ہو گیا۔"),
    ("late", "urdu_leyt", "Respelled لیٹھ (aspirated, in case لیٹ reads too short)",
     "یار، آج office میں کافی کام تھا، اس لیے میں لیٹھ ہو گیا۔"),
    ("late", "bare_latin", "Bare Latin `late` (isolated, for contrast)", "late"),
    ("late", "bare_urdu", "Bare لیٹ (isolated, for contrast)", "لیٹ"),

    # -- "database": expected to be a non-issue once production's lexicon runs.
    ("database", "verbatim", "Raw `database` — what A0 fed (NOT production)",
     "ہمیں database کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔"),
    ("database", "production", "`ڈیٹا base` — what production actually sends",
     "ہمیں ڈیٹا base کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔"),
    ("database", "all_urdu", "`ڈیٹا بیس` — the all-Urdu form §5c rejected (بیس = 'twenty')",
     "ہمیں ڈیٹا بیس کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔"),

    # -- control: the word §5b passed and the owner did not flag.
    ("office", "latin", "Latin `office` (control — should already be fine)",
     "یار، آج office میں کافی کام تھا، اس لیے میں دیر سے آیا۔"),
    ("office", "urdu", "Respelled آفس (control)",
     "یار، آج آفس میں کافی کام تھا، اس لیے میں دیر سے آیا۔"),
]

_GROUP_BLURB = {
    "late": "<b>The real finding.</b> `late` is not in <code>_LOANWORD_LEXICON</code>, and "
            "§5b recorded it as passing. If a respelling below sounds right, it earns a "
            "lexicon entry — by ear, which is the only way words get in there.",
    "database": "<b>Probably not a real defect.</b> A0 fed text verbatim and so skipped "
                "production's loanword lexicon. Compare the first two: if `ڈیٹا base` sounds "
                "right, production was already correct and A0 simply misrepresented it.",
    "office": "<b>Control.</b> §5b passed this word and you did not flag it. If it also sounds "
              "wrong, the problem is broader than a per-word lexicon can fix.",
}


def _write_page() -> Path:
    blocks = []
    for group in ("late", "database", "office"):
        rows = "".join(
            f"""
      <div class="row">
        <div class="lab">{escape(label)}</div>
        <div class="txt" dir="rtl">{escape(text)}</div>
        <audio controls src="{group}_{vid}.wav"></audio>
      </div>"""
            for g, vid, label, text in _CASES
            if g == group
        )
        blocks.append(
            f'<div class="grp"><h2>{escape(group)}</h2>'
            f'<p class="blurb">{_GROUP_BLURB[group]}</p>{rows}</div>'
        )

    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Loanword pronunciation — late / database</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto;
       padding: 0 1rem; background:#0b0d12; color:#e6e8ee; }}
h1 {{ font-size: 1.3rem; }}
h2 {{ font-size: 1.05rem; color:#6c8cff; margin-bottom:.3rem; }}
.grp {{ margin: 2rem 0; padding: 1rem; background:#161922; border-radius:8px; }}
.blurb {{ font-size:.9rem; color:#9aa4b2; margin-top:0; }}
.row {{ margin: 1rem 0; padding:.7rem; background:#1d2230; border-radius:6px; }}
.lab {{ font-size:.75rem; color:#e6b86c; text-transform:uppercase;
       letter-spacing:.04em; margin-bottom:.35rem; }}
.txt {{ font-size:1.1rem; margin:.3rem 0; }}
audio {{ width:100%; margin-top:.3rem; }}
</style></head>
<body>
<h1>Loanword pronunciation — following up your A0 listen</h1>
<p>You reported that column B (the Perso-Arabic gold) still mispronounces <b>late</b> and
<b>database</b>. Those turn out to be two different problems, so they are separated below.
Same reference voice and checkpoint as A0.</p>
{"".join(blocks)}
</body></html>
"""
    out = OUT_DIR / "listen.html"
    out.write_text(page, encoding="utf-8")
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if "--page-only" in sys.argv:
        print(f"wrote {_write_page()}", file=sys.stderr)
        return

    from app.inference.runtimes.omnivoice import OmniVoiceBackend

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", _HF_REPO, _HF_REVISION)

    manifest = []
    try:
        for group, vid, label, text in _CASES:
            out_path = OUT_DIR / f"{group}_{vid}.wav"
            print(f"synth {group}/{vid} ...", file=sys.stderr)
            backend.synth(
                text=text,
                reference_audio=str(_REF_AUDIO),
                output_path=str(out_path),
                params={"language": "ur"},
                sample_rate=24000,
                reference_text=_REF_TEXT,
            )
            manifest.append(
                {"group": group, "variant": vid, "label": label,
                 "text": text, "wav": out_path.name}
            )
    finally:
        backend.unload()

    (OUT_DIR / "manifest.json").write_text(
        json.dumps({"clips": manifest}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(manifest)} clips + {_write_page()}", file=sys.stderr)


if __name__ == "__main__":
    main()
