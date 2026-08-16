"""
Phase A3 -- the gate. Roman Urdu -> LLM conversion -> real OmniVoice audio ->
the owner's ear.

RUN 2 (2026-08-16): Ministral-3-8B, after the owner rejected run 1.
--------------------------------------------------------------------
Run 1 fed Qwen2.5-7B's `strict_zero_shot` output and the owner's verdict was
"column A is not usable". SS13 then re-ran A2 unchanged on
`mistralai/Ministral-3-8B-Instruct-2512` (Apache 2.0) and measured 74%
contract-clean at CER 0.0777 against Qwen's 46% / 0.2733. That is a different
class of result, so the gate is re-run rather than assumed to give the same
answer -- a text metric can only reject, never approve, and no one has heard
Ministral's output.

Run 1's clips and page are preserved at `eval/results/a3_full_chain/`; this
writes to `eval/results/a3_ministral/` so the two are directly comparable.

Everything before this point measured TEXT. A0 already showed why that is not
enough in the other direction (an ASR screen looked encouraging and the owner
heard a plain English accent). A2 measured a 46% contract pass rate, but a
contract score cannot answer the only question that matters here:

    Would fixing this suggestion by hand be less work than typing Urdu?

That is a judgement about the product, not the model, and it can only be made
by hearing what the converted text actually sounds like.

WHAT IS COMPARED
----------------
  A  Ministral `strict_few_shot` output -> OmniVoice   (the real pipeline)
  B  the corpus's Perso-Arabic gold      -> OmniVoice   (the ceiling)

Arm B is what the user would get by typing correct Urdu themselves, so it is
the thing arm A has to justify itself against -- not against silence, and not
against Roman-direct, which A0 already rejected.

The ten items keep run 1's deliberate mix: **six passed A2's contract and four
failed it**. A page of only the successes would answer a question nobody asked.
Eight items are held over from run 1 so the two pages can be compared clip for
clip; `technical` and `colloquial` had to be dropped because `strict_few_shot`
uses them as prompt exemplars, and scoring a model on its own examples is not
scoring. Their replacements are this arm's two *worst* items by CER
(`cs_06_interview` 0.340, `abbreviations` 0.281), which is the harder
substitution, not the kinder one.

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
OUT_DIR = _REPO_ROOT / "eval" / "results" / "a3_ministral"
_A2_MANIFEST = (
    _REPO_ROOT / "eval" / "results"
    / "roman_arabic_probe_mistralai_ministral_3_8b_instruct_2512" / "manifest.json"
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
#:
#: RUN 2: Ministral's best arm is `strict_few_shot` on every metric at once --
#: CER 0.0777, preservation 0.848, completeness 0.966, 29/39 contract-clean,
#: 0 unparseable. Nothing is traded away, so run 1's "best-reliability versus
#: best-CER" choice does not arise.
_A2_ARM = "strict_few_shot"

_NORMALIZATIONS = (TextNormalization.NUMBERS, TextNormalization.LOANWORD_LEXICON)

#: Six A2 contract passes, four failures -- run 1's mix, held to deliberately.
#: Eight items carry over so the two pages compare clip for clip.
#: `technical` and `colloquial` are `strict_few_shot` exemplars and scoring a
#: model on its own examples is not scoring; their replacements are this arm's
#: two WORST items by CER, which is the harder substitution, not the kinder one.
_ITEM_IDS = (
    "owner_01_sick",       # A2 fail: `office` kept Latin where gold converts it
    "owner_02_file",
    "owner_03_deadline",
    "owner_04_late",       # a Qwen contract FAILURE that Ministral passes at 0.038
    "owner_05_github",     # ditto at 0.045 -- Qwen broke these tokens mid-word
    "cs_06_interview",     # A2 fail, worst CER in the arm (0.340)
    "abbreviations",       # A2 fail, second-worst (0.281), `file` left in Latin
    "long_multiclause",
    "conv_01_greeting",
    "cs_04_laptop",        # A2 fail: `upgrade` translated away
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

        llm_text, _ = apply_text_normalizations(conv["parsed_output"], _NORMALIZATIONS)
        gold_text, _ = apply_text_normalizations(item["perso_arabic"], _NORMALIZATIONS)

        rows.append(
            {
                "id": item_id,
                "roman": item["roman"],
                "a2_contract_ok": conv["contract_ok"],
                "a2_cer": conv["cer"],
                "a2_lost": conv["lost_code_switch"],
                "a2_residue": conv["unconverted_residue"],
                "llm_raw": conv["parsed_output"],
                "llm_text": llm_text,
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
        <div class="text" dir="rtl">{escape(r["llm_text"])}</div>
        <audio controls src="{escape(r["id"])}_llm.wav"></audio>
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
<h1>A3, run 2 &mdash; same question, a different model</h1>
<p class="q"><b>You listened to run 1 and said "column A is not usable."</b> That was
Qwen2.5-7B: 46% of sentences fully clean, CER 0.27. Column A here is a different model
&mdash; <b>Ministral-3-8B</b> (Apache&nbsp;2.0), which scores <b>74% clean at CER 0.078</b>
on the identical corpus, prompt and metrics. Column B is unchanged: the hand-written Urdu
gold, i.e. what you'd get by typing correct Urdu yourself.<br><br>
The question is still <b>not</b> "is A perfect". It's: <b>would editing A be less work than
typing B yourself?</b> If yes, Phase B is worth building. If you'd still rather type Urdu,
it isn't, and the feature stays closed &mdash; this time on two models rather than one.</p>
<p><b>6 of these 10 passed A2's text contract and 4 failed it</b> &mdash; the failures are
labelled, so you can hear whether a "failed" conversion is actually unusable or merely imperfect.
That distinction is the whole decision. Both columns go through the same production normalization
(numbers spelled out, loanword lexicon applied), so this is what the app would really send.</p>
<p><b>Eight of the ten items are the same as run 1</b>, so you can open the two pages side by side
and compare clip for clip. <code>technical</code> and <code>colloquial</code> are gone because this
arm uses them as prompt examples &mdash; scoring a model on its own examples isn't scoring. Their
replacements, <code>cs_06_interview</code> and <code>abbreviations</code>, are this arm's two
<i>worst</i> items by CER, not two easy ones.</p>
<p class="caveat"><b>"contract OK" does not mean "the Urdu is correct", and "FAILED" does not mean
unusable.</b> The contract only checks that no English word was translated away and no Urdu was
left in Latin letters. It says nothing about whether the Urdu that came out is the right Urdu
&mdash; in run 1 <code>owner_01_sick</code> was badged OK while mangling <i>aaj</i> into
<span dir="rtl">امیدوار رہا کہ</span>. The reverse also happens here: three of the four failures
below (<code>owner_01_sick</code>, <code>abbreviations</code>, <code>cs_04_laptop</code>) failed
only because an English word went one way rather than the other, which may well sound
<i>better</i>. <b>Judge by ear; the badges are context, not a verdict.</b></p>
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
            for arm, text in (("llm", r["llm_text"]), ("gold", r["gold_text"])):
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
