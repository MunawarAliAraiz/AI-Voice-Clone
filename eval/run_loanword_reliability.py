"""
Blind repeat-sampling test for the loanword respellings.

WHY THIS EXISTS
---------------
On 2026-08-16 the owner judged `ڈیٹا base` as "data-boss" (wrong), and then,
roughly an hour later, judged it correct. Same text, same reference, same
checkpoint, same code path -- verified byte-identical from the two manifests.
The only difference was that they were two separate generations.

`OmniVoiceBackend.synth()` sets no seed. `self._model.generate(...)` samples
freshly on every call, so the pronunciation of a given loanword is a RANDOM
VARIABLE, not a property of the spelling.

That retroactively reframes every single-listen verdict in this project:

  - §5b "late passes"                  -> n=1
  - §5c "database respelling verified" -> n=1
  - A0's "late is wrong"               -> n=1
  - both database judgments above      -> n=1 each, and they disagree

None of those were wrong to record; they were just under-powered, and the
disagreement is the expected outcome rather than a contradiction to explain
away. It is also the simplest account of `late` passing in §5b, failing in
A0, and passing again on the focused re-listen.

WHAT THIS MEASURES
------------------
Not "is this respelling correct" but "how OFTEN is it correct" -- which is the
question that actually decides whether an entry belongs in
`_LOANWORD_LEXICON`. A spelling that is right 2 times in 4 is not a fix; it is
the same coin flip with extra steps.

BLIND BY CONSTRUCTION
---------------------
Clips are shuffled under a fixed seed and presented as bare numbers. The owner
has now twice rated the same audio differently, so knowing which clip is "the
one that ships" is exactly the bias worth removing. `key.json` maps clip
number -> variant and is written alongside, but nothing in the page reveals it.

Run on the pod:
    backend/.venv-omnivoice/bin/python eval/run_loanword_reliability.py
Then decode the owner's answer with:
    python eval/run_loanword_reliability.py --decode 3,7,12,...
"""

from __future__ import annotations

import json
import random
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "loanword_reliability"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)
_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"

#: Samples per variant. Four is coarse, but it separates "reliable" from
#: "coin flip", which is the only distinction that changes the decision.
_N = 4

#: Deterministic shuffle so the blind order is reproducible from the repo
#: alone -- the key file is a convenience, not the source of truth.
_SHUFFLE_SEED = 20260816

_DB_SENT = "ہمیں {} کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔"
_URL_SENT = "براہ کرم PDF فائل ای میل کر دیں اور {} بھی ساتھ بھیج دیں۔"

#: (variant_id, human description, text). Everything the owner marked correct
#: on the v3 page, plus the do-nothing control.
_VARIANTS: list[tuple[str, str, str]] = [
    ("db_verbatim", "database (Latin, verbatim) — the do-nothing control",
     _DB_SENT.format("database")),
    ("db_shipping", "ڈیٹا base — SHIPPING TODAY; rated wrong once, correct once",
     _DB_SENT.format("ڈیٹا base")),
    ("db_bari_ye", "ڈیٹا بےس", _DB_SENT.format("ڈیٹا بےس")),
    ("db_bays", "ڈیٹا bays", _DB_SENT.format("ڈیٹا bays")),
    ("db_bayss", "ڈیٹا bayss", _DB_SENT.format("ڈیٹا bayss")),
    ("db_dayta_base", "dayta base", _DB_SENT.format("dayta base")),
    ("url_verbatim", "URL (Latin, verbatim) — control", _URL_SENT.format("URL")),
    ("url_shipping", "یو آر ایل — SHIPPING TODAY", _URL_SENT.format("یو آر ایل")),
]


def _clip_plan() -> list[dict]:
    """Every (variant, sample) pair, shuffled into blind presentation order."""
    plan = [
        {"variant": vid, "desc": desc, "text": text, "sample": n,
         "wav": f"{vid}_s{n}.wav"}
        for vid, desc, text in _VARIANTS
        for n in range(1, _N + 1)
    ]
    random.Random(_SHUFFLE_SEED).shuffle(plan)
    for i, row in enumerate(plan, start=1):
        row["clip_no"] = i
    return plan


def _write_page(plan: list[dict]) -> Path:
    rows = "".join(
        f"""
  <div class="row">
    <div class="no">{row["clip_no"]}</div>
    <audio controls src="{escape(row["wav"])}"></audio>
  </div>"""
        for row in plan
    )
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Loanword reliability — blind</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 780px; margin: 2rem auto;
       padding: 0 1rem; background:#0b0d12; color:#e6e8ee; }}
h1 {{ font-size: 1.3rem; }}
.warn {{ background:#1d2230; border-left:3px solid #6c8cff; padding:.8rem 1rem;
        border-radius:4px; }}
.row {{ display:flex; align-items:center; gap:1rem; margin:.55rem 0;
       padding:.55rem .8rem; background:#161922; border-radius:6px; }}
.no {{ font-variant-numeric: tabular-nums; font-weight:700; color:#e6b86c;
      min-width:2.2rem; font-size:1.05rem; }}
audio {{ flex:1; }}
code {{ background:#1d2230; padding:.1rem .3rem; border-radius:3px; }}
</style></head>
<body>
<h1>Loanword reliability — blind listen</h1>
<p class="warn"><b>Why this is blind and repetitive.</b> You rated the same audio text
<i>wrong</i> once and <i>correct</i> once, about an hour apart. That is not an inconsistency
on your part — <code>OmniVoiceBackend</code> sets no seed, so every generation samples
differently and the pronunciation is a coin flip, not a property of the spelling. So each
spelling appears <b>{_N} times</b> here in scrambled order, and the labels are hidden so
knowing which one ships cannot sway the call.</p>
<p><b>What to do:</b> for each clip, decide only whether the loanword
(<i>database</i> or <i>URL</i>) is pronounced correctly. Ignore everything else.
Then send me the list of clip numbers that were <b>correct</b> — e.g. "3, 7, 12, 19".
I will decode which spelling each belonged to.</p>
<p>{len(plan)} clips.</p>
{rows}
</body></html>
"""
    out = OUT_DIR / "listen.html"
    out.write_text(page, encoding="utf-8")
    return out


def _decode(arg: str) -> None:
    key = json.loads((OUT_DIR / "key.json").read_text(encoding="utf-8"))
    by_no = {row["clip_no"]: row for row in key["plan"]}
    correct = {int(x) for x in arg.replace(" ", "").split(",") if x}
    unknown = correct - set(by_no)
    if unknown:
        raise SystemExit(f"no such clip numbers: {sorted(unknown)}")

    tally: dict[str, list[int]] = {vid: [] for vid, _, _ in _VARIANTS}
    for no, row in sorted(by_no.items()):
        if no in correct:
            tally[row["variant"]].append(no)

    desc = {vid: d for vid, d, _ in _VARIANTS}
    print(f"{'variant':<16} {'score':<8} description")
    print("-" * 72)
    for vid, _, _ in _VARIANTS:
        hits = len(tally[vid])
        print(f"{vid:<16} {hits}/{_N:<6} {desc[vid]}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plan = _clip_plan()

    if len(sys.argv) > 2 and sys.argv[1] == "--decode":
        _decode(sys.argv[2])
        return
    if "--page-only" in sys.argv:
        print(f"wrote {_write_page(plan)}", file=sys.stderr)
        return

    from app.inference.runtimes.omnivoice import OmniVoiceBackend

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", _HF_REPO, _HF_REVISION)

    try:
        for row in plan:
            print(f"synth clip {row['clip_no']:>2} ({row['variant']} s{row['sample']}) ...",
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

    (OUT_DIR / "key.json").write_text(
        json.dumps(
            {"note": "Blind key. The listening page never shows this.",
             "samples_per_variant": _N, "shuffle_seed": _SHUFFLE_SEED, "plan": plan},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(plan)} clips + {_write_page(plan)}", file=sys.stderr)


if __name__ == "__main__":
    main()
