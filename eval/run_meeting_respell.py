"""
Blind repeat-sampling test for `meeting` -- the one defect left after A3 passed.

WHY THIS ONE IS DIFFERENT FROM `database`
-----------------------------------------
The owner's single complaint on the A3 run-3 page (§15) was that
<meeting> is read as "mating". Two things make this a NEW kind of entry rather
than another row alongside `database`:

1. **The corpus gold has the identical defect.** `long_multiclause`'s gold
   writes میٹنگ, and so does Gemma-4-31B. Column B mispronounces it exactly as
   column A does, so this is not attributable to the transliteration model and
   would not have been fixed by choosing a different one.

2. **`_LOANWORD_LEXICON` cannot reach it.** Every entry there maps a LATIN key
   to an Urdu respelling (`database` -> ڈیٹا بےس). Here the text arrives
   already in Perso-Arabic, so a Latin key never matches. The dictionary
   (#103) therefore needs entries keyable on EITHER script, and this measurement
   is what pins down what the Perso-Arabic side of such an entry would contain.

WHAT IS BEING VARIED
--------------------
Only the vowel marking. میٹنگ's ی is ambiguous between "ee" and "ay", and
"mating" is what you get when the reader takes it as "ay". The candidates below
are the ordinary ways Urdu disambiguates that: an explicit kasra, a second ی, a
short vowel with no ی at all, and a word break.

Two controls are deliberately included and neither is expected to be the
answer:

  meet_verbatim_urdu  میٹنگ   -- what gold AND Gemma both produce today
  meet_latin          meeting -- what `cs_02_meeting` and `numd_01_time` gold
                                 use instead; §9e measured Latin loanwords
                                 mispronounced 17.2% of the time overall, so
                                 "leave it in Latin" is a real candidate here
                                 rather than a straw man

CARRIER SENTENCE
----------------
The clause the defect was actually heard in, trimmed to keep generation short:
`long_multiclause`'s opening. Same word, same position, same neighbours --
changing the carrier would change what is being measured.

BLIND BY CONSTRUCTION -- see run_loanword_reliability.py's docstring for the
full reasoning. Synthesis is unseeded (§9b), so a pronunciation is a random
variable and n=1 decides nothing: `database` needed twelve samples per
candidate, and the single best-SOUNDING clip in that set scored 4/12.

Run on the pod:
    backend/.venv-omnivoice/bin/python eval/run_meeting_respell.py
Then decode the owner's answer with:
    python eval/run_meeting_respell.py --decode "3,7,12"
"""

from __future__ import annotations

import json
import random
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "meeting_respell"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)
_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"

#: Distinct from run_loanword_reliability.py's seed so the two pages' clip
#: numbers do not line up -- a listener who remembered "7 was good" there
#: should get no signal from position here.
_SHUFFLE_SEED = 20260817

_SENT = "کل جب میں دفتر پہنچا تو پتہ چلا کہ {} ملتوی ہو گئی ہے۔"

#: (variant_id, what varies, the substituted token)
_VARIANTS: list[tuple[str, str, str]] = [
    ("meet_verbatim_urdu", "میٹنگ — CONTROL: what gold and Gemma both write today", "میٹنگ"),
    ("meet_latin", "meeting — CONTROL: Latin, as cs_02_meeting's gold does it", "meeting"),
    ("meet_kasra_meem", "مِیٹنگ — kasra on the meem", "مِیٹنگ"),
    ("meet_kasra_te", "میٹِنگ — kasra on the ٹ", "میٹِنگ"),
    ("meet_kasra_both", "مِیٹِنگ — kasra on both", "مِیٹِنگ"),
    ("meet_extra_ye", "میٹینگ — a second ی for the middle vowel", "میٹینگ"),
    ("meet_short_i", "مِٹنگ — short i, no ی at all", "مِٹنگ"),
    ("meet_split", "می ٹنگ — split at the word break", "می ٹنگ"),
]

_N = 4


def _clip_plan() -> list[dict]:
    plan = [
        {
            "variant": vid,
            "desc": desc,
            "token": token,
            "text": _SENT.format(token),
            "sample": n,
            "wav": f"{vid}_s{n}.wav",
        }
        for vid, desc, token in _VARIANTS
        for n in range(1, _N + 1)
    ]
    random.Random(_SHUFFLE_SEED).shuffle(plan)
    for i, row in enumerate(plan, start=1):
        row["clip_no"] = i
    return plan


def _write_page(plan: list[dict]) -> Path:
    rows = "".join(
        f'''
  <div class="row">
    <div class="no">{row["clip_no"]}</div>
    <audio controls src="{escape(row["wav"])}"></audio>
  </div>'''
        for row in plan
    )
    page = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>meeting — blind respelling test</title>
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
<h1>&ldquo;meeting&rdquo; &mdash; blind respelling test</h1>
<p class="warn"><b>Your one remaining complaint from the Gemma page.</b> Eight different
spellings of <i>meeting</i>, four generations each, in the sentence you heard it in
(&ldquo;&hellip;&rlm;<span dir="rtl">پتہ چلا کہ [meeting] ملتوی ہو گئی ہے</span>&rlm;&rdquo;),
scrambled with the labels hidden. Two of the eight are controls that I expect to fail,
including the spelling that is in use today &mdash; if a control scores well, that tells me
something too.</p>
<p><b>Why four of each and why blind:</b> synthesis is unseeded, so a pronunciation is a coin
flip rather than a property of the spelling. <code>database</code> needed twelve samples per
candidate to separate them, and the single best-<i>sounding</i> clip in that set scored 4/12
overall. A one-clip verdict here would be noise.</p>
<p><b>What to do:</b> for each clip decide only whether <i>meeting</i> sounds right
(&ldquo;mee-ting&rdquo;, not &ldquo;mating&rdquo;). Ignore everything else in the sentence.
Send me the clip numbers that were <b>correct</b> &mdash; e.g. &ldquo;3, 7, 12&rdquo;.</p>
<p>{len(plan)} clips.</p>
{rows}
</body></html>
'''
    out = OUT_DIR / "listen.html"
    out.write_text(page, encoding="utf-8")
    return out


def _decode(arg: str) -> None:
    """Map the owner's clip numbers back to variants and tally per spelling."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = json.loads((OUT_DIR / "key.json").read_text(encoding="utf-8"))
    by_no = {int(r["clip_no"]): r for r in key["plan"]}
    good = {int(x) for x in arg.replace(" ", "").split(",") if x}

    unknown = sorted(good - by_no.keys())
    if unknown:
        raise SystemExit(f"no such clip(s): {unknown} (page has 1..{len(by_no)})")

    tally: dict[str, list[int]] = {vid: [] for vid, _, _ in _VARIANTS}
    for no in sorted(good):
        tally[by_no[no]["variant"]].append(no)

    desc = {vid: d for vid, d, _ in _VARIANTS}
    print(f"{len(good)} of {len(by_no)} clips marked correct\n")
    for vid, _, _ in _VARIANTS:
        hits = tally[vid]
        print(f"  {len(hits)}/{_N}  {desc[vid]}")
        if hits:
            print(f"        clips {', '.join(map(str, hits))}")


def main() -> None:
    if "--decode" in sys.argv:
        _decode(sys.argv[sys.argv.index("--decode") + 1])
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plan = _clip_plan()

    if "--page-only" in sys.argv:
        print(f"wrote {_write_page(plan)}", file=sys.stderr)
        return

    from app.inference.runtimes.omnivoice import OmniVoiceBackend

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", _HF_REPO, _HF_REVISION)
    try:
        for row in plan:
            print(f"synth {row['wav']} ...", file=sys.stderr)
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

    # The key is written AFTER the audio, so a crashed run cannot leave a key
    # that claims clips which do not exist.
    (OUT_DIR / "key.json").write_text(
        json.dumps({"seed": _SHUFFLE_SEED, "n": _N, "carrier": _SENT, "plan": plan},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(plan)} clips + {_write_page(plan)}", file=sys.stderr)


if __name__ == "__main__":
    main()
