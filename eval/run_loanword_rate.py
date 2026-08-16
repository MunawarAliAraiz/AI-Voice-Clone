"""
How often does OmniVoice actually mispronounce an English loanword?

docs/URDU_BAKEOFF_RESULTS.md §9d asks this before anyone builds a user-editable
pronunciation dictionary. `_LOANWORD_LEXICON` is a two-entry hardcoded dict and
a new word costs the owner a dozen blind listens, so the design question is
whether that even matters -- which depends entirely on a number nobody has
measured. If two words in twenty fail, the shipped defaults plus the editable
Composer box are a complete answer and a dictionary is over-engineering. If a
quarter fail, it is not.

WHAT IS MEASURED
----------------
The 20 corpus items carrying Latin islands (60 distinct word instances), run
through the **real production normalization path** -- `apply_text_normalizations`
with exactly the kinds `OMNIVOICE_URDU` declares, `(NUMBERS, LOANWORD_LEXICON)`.
That matters: A0 fed text verbatim, bypassed the lexicon, and produced a
`database` complaint about input production never sends. This measures what
users actually get, including the freshly-corrected `ڈیٹا بےس`.

WHY n=2 AND WHY NOT BLIND
-------------------------
Per §9b synthesis is unseeded, so any one clip is a draw. But the quantity
wanted here is the **per-generation failure rate**, and a user also gets one
draw -- so independent clips across many different words estimate it directly,
and breadth buys more than repetition. n=2 gives ~120 word observations from 40
clips, enough to separate "5%" from "25%", which is the only distinction that
changes the decision.

Blinding is pointless here and would be actively unhelpful: there are no
competing variants to be biased between, and the listener has to be told which
word to judge. Naming it reveals no condition.

Run on the pod:
    backend/.venv-omnivoice/bin/python eval/run_loanword_rate.py
Then, once the owner reports the failures:
    python eval/run_loanword_rate.py --score "3:office, 11:RAM, 11:CV"
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.urdu_text import (  # noqa: E402
    TextNormalization,
    apply_text_normalizations,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "loanword_rate"
_CORPUS = _REPO_ROOT / "eval" / "fixtures" / "urdu_corpus.json"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)
_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"

#: Exactly what OMNIVOICE_URDU declares in catalog.py. Kept as a literal rather
#: than imported from the catalog so this stays a pure text-side script -- but
#: if the spec ever changes, this must change with it.
_NORMALIZATIONS = (TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON)

_N = 2
_LATIN = re.compile(r"[A-Za-z][A-Za-z.]*")


def _items() -> list[dict]:
    corpus = json.loads(_CORPUS.read_text(encoding="utf-8"))
    out = []
    for item in corpus["items"]:
        normalized, applied = apply_text_normalizations(item["perso_arabic"], _NORMALIZATIONS)
        words = _LATIN.findall(normalized)
        if not words:
            continue
        out.append(
            {
                "id": item["id"],
                "raw": item["perso_arabic"],
                "text": normalized,
                "normalizations_applied": [str(a) for a in applied],
                "latin_words": words,
            }
        )
    return out


def _plan(items: list[dict]) -> list[dict]:
    plan = []
    for item in items:
        for n in range(1, _N + 1):
            plan.append({**item, "sample": n, "wav": f"{item['id']}_s{n}.wav"})
    for i, row in enumerate(plan, start=1):
        row["clip_no"] = i
    return plan


def _write_page(plan: list[dict]) -> Path:
    total_words = sum(len(r["latin_words"]) for r in plan)
    rows = "".join(
        f"""
  <div class="row">
    <div class="head"><span class="no">{row["clip_no"]}</span>
      <span class="words">{escape(" · ".join(row["latin_words"]))}</span>
      <span class="id">{escape(row["id"])}{" (2nd take)" if row["sample"] == 2 else ""}</span></div>
    <div class="txt" dir="rtl">{escape(row["text"])}</div>
    <audio controls src="{escape(row["wav"])}"></audio>
  </div>"""
        for row in plan
    )
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Loanword failure rate</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto;
       padding: 0 1rem; background:#0b0d12; color:#e6e8ee; }}
h1 {{ font-size: 1.3rem; }}
.warn {{ background:#1d2230; border-left:3px solid #6c8cff; padding:.8rem 1rem;
        border-radius:4px; }}
.row {{ margin:.8rem 0; padding:.7rem .9rem; background:#161922; border-radius:6px; }}
.head {{ display:flex; align-items:baseline; gap:.8rem; margin-bottom:.35rem; }}
.no {{ font-variant-numeric: tabular-nums; font-weight:700; color:#e6b86c;
      min-width:2.2rem; font-size:1.05rem; }}
.words {{ font-weight:600; color:#8fd3a6; }}
.id {{ margin-left:auto; font-size:.78rem; color:#6f7a8a; }}
.txt {{ font-size:1.05rem; margin:.3rem 0; color:#c9cfda; }}
audio {{ width:100%; margin-top:.3rem; }}
code {{ background:#1d2230; padding:.1rem .3rem; border-radius:3px; }}
</style></head>
<body>
<h1>How often is an English loanword mispronounced?</h1>
<p class="warn">This decides whether the hardcoded <code>_LOANWORD_LEXICON</code> needs to become a
user-editable dictionary, or whether the shipped defaults plus the editable text box are already
enough. <b>{len(plan)} clips, {total_words} word instances</b>, every item run through the real
production path (numbers + lexicon, so <code>database</code> is already the corrected
<code>ڈیٹا بےس</code>). Each item appears twice because synthesis is unseeded and one clip is a draw.</p>
<p><b>What to do:</b> the green words are the ones to judge — ignore the rest of the sentence.
Most should be fine. <b>Send me only the failures</b>, as <code>clip:word</code> pairs —
e.g. <code>3:office, 11:RAM, 11:CV</code>. If a clip is entirely fine, skip it.</p>
{rows}
</body></html>
"""
    out = OUT_DIR / "listen.html"
    out.write_text(page, encoding="utf-8")
    return out


def _score(arg: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    plan = manifest["plan"]
    by_no = {row["clip_no"]: row for row in plan}

    failures: list[tuple[int, str]] = []
    for token in (t.strip() for t in arg.split(",")):
        if not token:
            continue
        clip_s, _, word = token.partition(":")
        clip = int(clip_s.strip())
        if clip not in by_no:
            raise SystemExit(f"no such clip number: {clip}")
        failures.append((clip, word.strip()))

    total_clips = len(plan)
    total_words = sum(len(r["latin_words"]) for r in plan)
    distinct = {w for r in plan for w in r["latin_words"]}
    bad_clips = {c for c, _ in failures}
    bad_words = Counter(w for _, w in failures)

    print(f"clips           : {len(bad_clips)}/{total_clips} contained at least one bad word "
          f"({len(bad_clips) / total_clips:.1%})")
    print(f"word instances  : {len(failures)}/{total_words} mispronounced "
          f"({len(failures) / total_words:.1%})")
    print(f"distinct words  : {len(bad_words)}/{len(distinct)} affected "
          f"({len(bad_words) / len(distinct):.1%})  <- one lexicon entry each")
    print()
    if not bad_words:
        print("no failures reported.")
        return

    # A word failing every take is systematically wrong and a lexicon entry can
    # fix it. A word failing some takes is the unseeded coin flip of §9b, and no
    # spelling choice will fully fix it -- the two need different responses, so
    # they are never pooled into one number here.
    always, sometimes = [], []
    for word, n in bad_words.most_common():
        occurrences = sum(r["latin_words"].count(word) for r in plan)
        (always if n == occurrences else sometimes).append((word, n, occurrences))

    if always:
        print(f"ALWAYS wrong ({len(always)}) -- deterministic, a lexicon entry fixes these:")
        for word, n, occ in always:
            print(f"  {word:<16} {n}/{occ}")
    if sometimes:
        print(f"\nSOMETIMES wrong ({len(sometimes)}) -- unseeded variance, not a spelling problem:")
        for word, n, occ in sometimes:
            print(f"  {word:<16} {n}/{occ}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    items = _items()
    plan = _plan(items)

    argv = sys.argv[1:]
    if "--score" in argv:
        _score(argv[argv.index("--score") + 1])
        return
    if "--page-only" in argv:
        print(f"wrote {_write_page(plan)}", file=sys.stderr)
        return

    from app.inference.runtimes.omnivoice import OmniVoiceBackend

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", _HF_REPO, _HF_REVISION)

    try:
        for row in plan:
            print(f"synth clip {row['clip_no']:>2} ({row['id']} s{row['sample']}) ...",
                  file=sys.stderr)
            backend.synth(
                text=row["text"],
                reference_audio=str(_REF_AUDIO),
                output_path=str(OUT_DIR / row["wav"]),
                params={"language": "ur"},
                sample_rate=24000,
                reference_text=_REF_TEXT,
            )
    finally:
        backend.unload()

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(
            {"normalizations": [str(n) for n in _NORMALIZATIONS],
             "samples_per_item": _N, "plan": plan},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(plan)} clips + {_write_page(plan)}", file=sys.stderr)


if __name__ == "__main__":
    main()
