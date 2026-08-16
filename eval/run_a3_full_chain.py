"""
Phase A3 -- the gate. Roman Urdu -> Qwen conversion -> real OmniVoice audio ->
the owner's ear.

Everything before this point measured TEXT. A0 already showed why that is not
enough in the other direction (an ASR screen looked encouraging and the owner
heard a plain English accent). A2 measured a 46% contract pass rate, but a
contract score cannot answer the only question that matters here:

    Would fixing this suggestion by hand be less work than typing Urdu?

That is a judgement about the product, not the model, and it can only be made
by hearing what the converted text actually sounds like.

WHAT IS COMPARED
----------------
  A  Qwen 7B `strict_zero_shot` output -> OmniVoice   (the real pipeline)
  B  the corpus's Perso-Arabic gold    -> OmniVoice   (the ceiling)

Arm B is what the user would get by typing correct Urdu themselves, so it is
the thing arm A has to justify itself against -- not against silence, and not
against Roman-direct, which A0 already rejected.

The ten items are a deliberate mix: **six passed A2's contract and four failed
it** (`owner_04_late`, `owner_05_github`, `technical`, `cs_04_laptop`). A page
of only the successes would answer a question nobody asked.

The conversions are read from A2's committed manifest rather than regenerated,
so this is scoring the exact outputs §10's numbers describe. Nothing is
re-derived, and no second sampling of the LLM can quietly change the subject.

BOTH ARMS GO THROUGH PRODUCTION NORMALIZATION
----------------------------------------------
`apply_text_normalizations` with the kinds `OMNIVOICE_URDU` declares, exactly
as A0 should have done. A0 fed verbatim text, bypassed the loanword lexicon,
and produced a complaint about `database` that production never actually sends.
Not repeating that.

Run on the pod:
    backend/.venv-omnivoice/bin/python eval/run_a3_full_chain.py
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.urdu_text import (  # noqa: E402
    TextNormalization,
    apply_text_normalizations,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "a3_full_chain"
_A2_MANIFEST = (
    _REPO_ROOT / "eval" / "results"
    / "roman_arabic_probe_qwen_qwen2_5_7b_instruct" / "manifest.json"
)
_CORPUS = _REPO_ROOT / "eval" / "fixtures" / "urdu_corpus.json"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)
_HF_REPO = "k2-fsa/OmniVoice"
_HF_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"

#: Best-reliability A2 arm: 0 unparseable at 7B, and 62% contract-clean on the
#: trusted original-13 subset. Not the best mean CER -- that was
#: strict_few_shot, which has the WORST contract rate (§10b).
_A2_ARM = "strict_zero_shot"

_NORMALIZATIONS = (TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON)

#: Six A2 contract passes, four failures. Chosen before any audio existed.
_ITEM_IDS = (
    "owner_01_sick",
    "owner_02_file",
    "owner_03_deadline",
    "owner_04_late",       # A2 fail: office/late translated away
    "owner_05_github",     # A2 fail: tokens broken mid-word
    "technical",           # A2 fail: all four English words translated
    "colloquial",
    "long_multiclause",
    "conv_01_greeting",
    "cs_04_laptop",        # A2 fail
)


def _build_rows() -> list[dict]:
    a2 = json.loads(_A2_MANIFEST.read_text(encoding="utf-8"))
    corpus = {i["id"]: i for i in json.loads(_CORPUS.read_text(encoding="utf-8"))["items"]}
    conversions = {
        r["item_id"]: r for r in a2["results"] if r["variant"] == _A2_ARM
    }

    rows = []
    for item_id in _ITEM_IDS:
        conv = conversions[item_id]
        if conv["status"] != "ok":
            raise SystemExit(f"{item_id} was unparseable in A2 -- pick another item")
        item = corpus[item_id]

        qwen_text, _ = apply_text_normalizations(conv["parsed_output"], _NORMALIZATIONS)
        gold_text, _ = apply_text_normalizations(item["perso_arabic"], _NORMALIZATIONS)

        rows.append(
            {
                "id": item_id,
                "roman": item["roman"],
                "a2_contract_ok": conv["contract_ok"],
                "a2_cer": conv["cer"],
                "a2_lost": conv["lost_code_switch"],
                "a2_residue": conv["unconverted_residue"],
                "qwen_raw": conv["parsed_output"],
                "qwen_text": qwen_text,
                "gold_raw": item["perso_arabic"],
                "gold_text": gold_text,
            }
        )
    return rows


def _write_page(rows: list[dict]) -> Path:
    blocks = []
    for r in rows:
        badge = (
            '<span class="pass">A2: contract OK</span>' if r["a2_contract_ok"]
            else '<span class="fail">A2: contract FAILED</span>'
        )
        why = ""
        if r["a2_lost"]:
            why += f' translated away: {escape(", ".join(r["a2_lost"]))}.'
        if r["a2_residue"]:
            why += f' left in Latin: {escape(", ".join(r["a2_residue"][:5]))}.'

        blocks.append(f"""
  <div class="item">
    <div class="meta"><b>{escape(r["id"])}</b> {badge}
      <span class="cer">CER {r["a2_cer"]:.3f}</span></div>
    <div class="roman">You typed: <b>{escape(r["roman"])}</b></div>
    <div class="pair">
      <div class="col">
        <div class="label">A &mdash; what the feature would produce</div>
        <div class="text" dir="rtl">{escape(r["qwen_text"])}</div>
        <audio controls src="{escape(r["id"])}_qwen.wav"></audio>
        <div class="note">{why or "&nbsp;"}</div>
      </div>
      <div class="col">
        <div class="label">B &mdash; typing correct Urdu yourself (the ceiling)</div>
        <div class="text" dir="rtl">{escape(r["gold_text"])}</div>
        <audio controls src="{escape(r["id"])}_gold.wav"></audio>
        <div class="note">&nbsp;</div>
      </div>
    </div>
  </div>""")

    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>A3 &mdash; the full chain</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 2rem auto;
       padding: 0 1rem; background:#0b0d12; color:#e6e8ee; }}
h1 {{ font-size: 1.3rem; }}
.q {{ background:#1d2230; border-left:3px solid #6c8cff; padding:.9rem 1.1rem;
     border-radius:4px; }}
.item {{ margin: 1.6rem 0; padding: 1rem; background:#161922; border-radius:8px; }}
.meta {{ font-size:.9rem; color:#9aa4b2; margin-bottom:.5rem; }}
.roman {{ font-size:1rem; color:#c9cfda; margin-bottom:.7rem; padding:.4rem .6rem;
         background:#0f121a; border-radius:4px; }}
.pair {{ display:flex; gap:1rem; }}
.col {{ flex:1; }}
.label {{ font-size:.75rem; color:#6c8cff; text-transform:uppercase;
         letter-spacing:.04em; margin-bottom:.3rem; }}
.text {{ font-size:1.1rem; margin:.4rem 0; min-height:3.2em; }}
.note {{ font-size:.8rem; color:#e0846c; min-height:1.2em; margin-top:.3rem; }}
.pass {{ color:#8fd3a6; font-weight:600; }}
.fail {{ color:#e0846c; font-weight:600; }}
.cer {{ color:#6f7a8a; margin-left:.5rem; }}
.caveat {{ background:#2a1d1d; border-left:3px solid #e0846c; padding:.9rem 1.1rem;
          border-radius:4px; font-size:.95rem; }}
audio {{ width:100%; margin-top:.3rem; }}
</style></head>
<body>
<h1>A3 &mdash; does the Roman&rarr;Urdu feature actually save you work?</h1>
<p class="q"><b>The question is not "is A perfect".</b> It won't be &mdash; A2 measured 46%
of sentences coming out fully clean. The question is:
<b>would editing A be less work than typing B yourself?</b> If yes, the feature is worth
building even at 46%. If you'd rather just type Urdu, it isn't, and Phase B should not start.</p>
<p><b>6 of these 10 passed A2's text contract and 4 failed it</b> &mdash; the failures are
labelled, so you can hear whether a "failed" conversion is actually unusable or merely imperfect.
That distinction is the whole decision. Both columns go through the same production normalization
(numbers spelled out, loanword lexicon applied), so this is what the app would really send.</p>
<p class="caveat"><b>"contract OK" does not mean "the Urdu is correct."</b> It means only that no
English word was translated away and no Urdu was left sitting in Latin letters. It says nothing
about whether the Urdu that came out is the right Urdu. <b><code>owner_01_sick</code>, the very
first item below, is the proof</b>: it is badged contract OK, and its output mangles
<i>aaj</i> into <span dir="rtl">امیدوار رہا کہ</span> and leaks a Devanagari
<span dir="rtl">हे</span>. Its CER of 0.406 is the number that caught it. Across the whole arm the
two metrics do agree &mdash; contract-passing items have a median CER of 0.188 against 0.324 for
failures, and this is the only contract-pass above 0.30 &mdash; so treat the badge and the CER
together, and your ear over both.</p>
{"".join(blocks)}
</body></html>
"""
    out = OUT_DIR / "listen.html"
    out.write_text(page, encoding="utf-8")
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = _build_rows()

    if "--page-only" in sys.argv:
        print(f"wrote {_write_page(rows)}", file=sys.stderr)
        return

    from app.inference.runtimes.omnivoice import OmniVoiceBackend

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", _HF_REPO, _HF_REVISION)

    try:
        for r in rows:
            for arm, text in (("qwen", r["qwen_text"]), ("gold", r["gold_text"])):
                print(f"synth {r['id']} [{arm}] ...", file=sys.stderr)
                backend.synth(
                    text=text,
                    reference_audio=str(_REF_AUDIO),
                    output_path=str(OUT_DIR / f"{r['id']}_{arm}.wav"),
                    params={"language": "ur"},
                    sample_rate=24000,
                    reference_text=_REF_TEXT,
                )
    finally:
        backend.unload()

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(
            {"a2_arm": _A2_ARM, "a2_source": str(_A2_MANIFEST.relative_to(_REPO_ROOT)),
             "normalizations": [str(n) for n in _NORMALIZATIONS], "rows": rows},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(rows) * 2} clips + {_write_page(rows)}", file=sys.stderr)


if __name__ == "__main__":
    main()
