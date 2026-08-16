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
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)
_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"

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

# ── Rounds ──────────────────────────────────────────────────────────────────
#
# Round 1 (n=4, all 8 variants) answered the coarse question and settled URL
# outright: `یو آر ایل` 4/4 against verbatim `URL` 0/4. It could NOT settle
# `database` -- `ڈیٹا بےس` 3/4 versus the shipping `ڈیٹا base` 2/4 is well
# inside the noise of four samples, and one of those two hits was qualified
# ("somehow"). Two other things it did establish: verbatim `database` is
# reliably wrong (0/4), so a lexicon entry is justified rather than assumed;
# and `ڈیٹا bays` produced the owner's single best-sounding clip while scoring
# 1/4 overall -- high ceiling, low reliability, and a clean illustration of why
# a one-clip verdict misleads.
#
# Round 2 raises n on exactly the three candidates that are still live, and
# keeps verbatim as an anchor so a drift in the owner's criteria between
# sessions would show up rather than hide.
_ROUNDS: dict[str, dict[str, object]] = {
    "1": {"variants": [v for v, _, _ in _VARIANTS], "n": 4, "suffix": ""},
    "2": {
        "variants": ["db_bari_ye", "db_shipping", "db_bays", "db_verbatim"],
        "n": {"db_bari_ye": 8, "db_shipping": 8, "db_bays": 8, "db_verbatim": 4},
        "suffix": "_round2",
    },
}


def _round(name: str) -> dict:
    if name not in _ROUNDS:
        raise SystemExit(f"unknown round {name!r}; known: {sorted(_ROUNDS)}")
    return _ROUNDS[name]


def _out_dir(cfg: dict) -> Path:
    return _REPO_ROOT / "eval" / "results" / f"loanword_reliability{cfg['suffix']}"


def _n_for(cfg: dict, variant: str) -> int:
    n = cfg["n"]
    return n[variant] if isinstance(n, dict) else int(n)


def _clip_plan(cfg: dict) -> list[dict]:
    """Every (variant, sample) pair for this round, shuffled into blind order."""
    by_id = {vid: (desc, text) for vid, desc, text in _VARIANTS}
    plan = [
        {"variant": vid, "desc": by_id[vid][0], "text": by_id[vid][1], "sample": n,
         "wav": f"{vid}_s{n}.wav"}
        for vid in cfg["variants"]
        for n in range(1, _n_for(cfg, vid) + 1)
    ]
    # Seeded per round, so round 2's order is not a rotation of round 1's --
    # a listener who remembered position 7 would otherwise carry that over.
    random.Random(_SHUFFLE_SEED + len(plan)).shuffle(plan)
    for i, row in enumerate(plan, start=1):
        row["clip_no"] = i
    return plan


def _write_page(plan: list[dict], cfg: dict, round_name: str) -> Path:
    rows = "".join(
        f'''
  <div class="row">
    <div class="no">{row["clip_no"]}</div>
    <audio controls src="{escape(row["wav"])}"></audio>
  </div>'''
        for row in plan
    )
    if round_name == "1":
        why = (
            "<b>Why this is blind and repetitive.</b> You rated the same audio text "
            "<i>wrong</i> once and <i>correct</i> once, about an hour apart. That is not an "
            "inconsistency on your part — <code>OmniVoiceBackend</code> sets no seed, so every "
            "generation samples differently and the pronunciation is a coin flip, not a "
            "property of the spelling. Each spelling appears several times here in scrambled "
            "order, with labels hidden."
        )
    else:
        why = (
            "<b>Round 2 — the tie-break.</b> Round 1 settled <code>URL</code> outright "
            "(<code>یو آر ایل</code> 4/4 against verbatim 0/4) but could not settle "
            "<code>database</code>: 3/4 versus 2/4 across four samples is inside the noise. "
            "The three live candidates now get <b>8 samples each</b>, plus 4 of the verbatim "
            "control as an anchor — if your criteria have shifted since round 1, the anchor "
            "will show it rather than hide it. Freshly shuffled, so clip numbers do not "
            "correspond to round 1."
        )

    page = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Loanword reliability — blind (round {round_name})</title>
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
<h1>Loanword reliability — blind listen (round {round_name})</h1>
<p class="warn">{why}</p>
<p><b>What to do:</b> for each clip, decide only whether the loanword
(<i>database</i> or <i>URL</i>) is pronounced correctly. Ignore everything else.
Then send me the list of clip numbers that were <b>correct</b> — e.g. "3, 7, 12, 19".</p>
<p>{len(plan)} clips.</p>
{rows}
</body></html>
'''
    out = _out_dir(cfg) / "listen.html"
    out.write_text(page, encoding="utf-8")
    return out


def _decode(arg: str, cfg: dict) -> None:
    # Windows consoles default to cp1252, which cannot encode the Urdu in the
    # variant descriptions. Same guard as urdu_represent.py's __main__ -- the
    # decode step runs on the local planning box, so this is the normal case.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    key = json.loads((_out_dir(cfg) / "key.json").read_text(encoding="utf-8"))
    by_no = {row["clip_no"]: row for row in key["plan"]}
    correct = {int(x) for x in arg.replace(" ", "").split(",") if x}
    unknown = correct - set(by_no)
    if unknown:
        raise SystemExit(f"no such clip numbers: {sorted(unknown)}")

    desc = {vid: d for vid, d, _ in _VARIANTS}
    print(f"{'variant':<16} {'score':<8} description")
    print("-" * 72)
    for vid in cfg["variants"]:
        hits = sum(1 for no, r in by_no.items() if r["variant"] == vid and no in correct)
        print(f"{vid:<16} {f'{hits}/{_n_for(cfg, vid)}':<8} {desc[vid]}")


def main() -> None:
    argv = sys.argv[1:]
    round_name = "1"
    if "--round" in argv:
        round_name = argv[argv.index("--round") + 1]
    cfg = _round(round_name)
    out_dir = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = _clip_plan(cfg)

    if "--decode" in argv:
        _decode(argv[argv.index("--decode") + 1], cfg)
        return
    if "--page-only" in argv:
        print(f"wrote {_write_page(plan, cfg, round_name)}", file=sys.stderr)
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
                output_path=str(out_dir / row["wav"]),
                params={"language": "ur"},
                sample_rate=24000,
                reference_text=_REF_TEXT,
            )
    finally:
        backend.unload()

    (out_dir / "key.json").write_text(
        json.dumps(
            {"note": "Blind key. The listening page never shows this.",
             "round": round_name, "shuffle_seed": _SHUFFLE_SEED, "plan": plan},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(plan)} clips + {_write_page(plan, cfg, round_name)}", file=sys.stderr)


if __name__ == "__main__":
    main()
