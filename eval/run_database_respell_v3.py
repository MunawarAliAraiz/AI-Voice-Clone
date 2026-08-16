"""
Third attempt at `database`, after the owner's 2026-08-16 listen rejected all
three forms tried so far:

    database        (Latin, verbatim)   -> "Da-ta-base"  (syllables separated)
    ڈیٹا base       (SHIPPING TODAY)    -> "data-boss"   (Latin "base" -> "boss")
    ڈیٹا بیس        (all-Urdu)          -> "data-bes"    (بیس = "twenty")

**This makes `_LOANWORD_LEXICON["database"] = "ڈیٹا base"` a live production
bug.** docs/URDU_BAKEOFF_RESULTS.md §5c recorded that respelling as verified
correct in this exact sentence; it does not survive re-listen. Same pattern as
`late` in §5b — a recorded pass that fails when heard again — which is the
second instance in two days and the reason a pass is never trusted here.

The failures are informative rather than random, and the variants below are
chosen against them:

- Latin `base` attracts an English "boss" vowel  -> try Urdu spellings that
  pin the /eɪ/ instead (بَیس with zabar, بےس with bari ye, بیز voiced).
- `بیس` collides with the Urdu word for twenty   -> try removing the word
  boundary that lets it be read as a standalone word (`ڈیٹابیس`, one token).
- Latin `database` is split into syllables       -> try `data base` as two
  Latin words, which is how the syllable break wants to fall anyway.

`URL` is included as a control, and this is not idle. It is the *other* entry
in `_LOANWORD_LEXICON`, verified by the same §5c listen that just failed for
`database`. If it is also wrong now, the problem is the lexicon approach, not
the spelling of one word — and that is a much bigger finding than a bad entry.

Everything is synthesized inside its realistic corpus sentence. §5c already
established that bare and short-context forms are unreliable for every
loanword, and the owner's `late` listen confirmed it again (isolated `late`
wrong, in-sentence `late` fine) — so isolated forms would only add noise.

Run on the pod:
    backend/.venv-omnivoice/bin/python eval/run_database_respell_v3.py
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "database_respell_v3"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)
_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"

# The corpus `technical` item, with only the database token swapped.
_SENT = "ہمیں {} کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔"

# (group, variant_id, label, text)
_CASES: list[tuple[str, str, str, str]] = [
    ("database", "v1_latin", "`database` — Latin, verbatim (you heard: Da-ta-base)",
     _SENT.format("database")),
    ("database", "v2_shipping", "`ڈیٹا base` — SHIPPING TODAY (you heard: data-boss)",
     _SENT.format("ڈیٹا base")),
    ("database", "v3_all_urdu", "`ڈیٹا بیس` — all-Urdu (you heard: data-bes)",
     _SENT.format("ڈیٹا بیس")),
    ("database", "v4_one_token", "`ڈیٹابیس` — one token, no space (kills the 'twenty' word boundary)",
     _SENT.format("ڈیٹابیس")),
    ("database", "v5_zabar", "`ڈیٹا بَیس` — zabar on the ب to pin the /eɪ/",
     _SENT.format("ڈیٹا بَیس")),
    ("database", "v6_bari_ye", "`ڈیٹا بےس` — bari ye for the /eɪ/",
     _SENT.format("ڈیٹا بےس")),
    ("database", "v7_voiced", "`ڈیٹا بیز` — voiced ending, as many speakers say it",
     _SENT.format("ڈیٹا بیز")),
    ("database", "v8_two_latin", "`data base` — two Latin words (lets the syllable break fall right)",
     _SENT.format("data base")),
    ("database", "v9_mixed_rev", "`data بیس` — Latin data, Urdu base (mirror of what ships)",
     _SENT.format("data بیس")),
    ("database", "v10_daata", "`ڈاٹا بیس` — the other common Urdu spelling of 'data'",
     _SENT.format("ڈاٹا بیس")),

    # -- control: the OTHER lexicon entry, verified by the same §5c listen.
    ("url", "latin", "`URL` — Latin, verbatim",
     "براہ کرم PDF فائل ای میل کر دیں اور URL بھی ساتھ بھیج دیں۔"),
    ("url", "shipping", "`یو آر ایل` — SHIPPING TODAY (control: is the other entry still good?)",
     "براہ کرم PDF فائل ای میل کر دیں اور یو آر ایل بھی ساتھ بھیج دیں۔"),
]

_BLURB = {
    "database": "All three forms tried so far are rejected. Pick whichever of these says "
                "<b>DAY-ta-bayss</b>. If none does, say so — the honest answer may be that "
                "OmniVoice cannot say this word, and the entry should be removed rather than "
                "left shipping a wrong one.",
    "url": "<b>Control, and the important one.</b> This is the only other entry in the "
           "lexicon, verified by the same listen that just failed for <code>database</code>. "
           "If <code>یو آر ایل</code> is also wrong, the per-word lexicon idea is what is "
           "broken, not one spelling.",
}


def _write_page() -> Path:
    blocks = []
    for group in ("database", "url"):
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
            f'<p class="blurb">{_BLURB[group]}</p>{rows}</div>'
        )

    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>database — respelling attempt 3</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto;
       padding: 0 1rem; background:#0b0d12; color:#e6e8ee; }}
h1 {{ font-size: 1.3rem; }}
h2 {{ font-size: 1.05rem; color:#6c8cff; margin-bottom:.3rem; }}
.grp {{ margin: 2rem 0; padding: 1rem; background:#161922; border-radius:8px; }}
.blurb {{ font-size:.9rem; color:#9aa4b2; margin-top:0; }}
.row {{ margin: 1rem 0; padding:.7rem; background:#1d2230; border-radius:6px; }}
.lab {{ font-size:.78rem; color:#e6b86c; margin-bottom:.35rem; }}
.txt {{ font-size:1.1rem; margin:.3rem 0; }}
audio {{ width:100%; margin-top:.3rem; }}
.warn {{ background:#2a1d1d; border-left:3px solid #e06c6c; padding:.8rem 1rem;
        border-radius:4px; }}
</style></head>
<body>
<h1>`database` — third attempt</h1>
<p class="warn"><b>What your last listen established:</b> all three forms tried so far are wrong,
including <code>ڈیٹا base</code> — which is what the app ships right now. So this is a live bug,
not just an unfinished experiment. Every clip below is inside the same realistic sentence, since
isolated words are unreliable for every loanword (your `late` listen showed that again).</p>
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
