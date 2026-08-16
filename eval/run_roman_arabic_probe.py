"""
Roman Urdu -> Perso-Arabic Urdu capability probe (Qwen2.5-Instruct).

NOT the Devanagari probe (`eval/run_translit_probe.py`) rerun with a different
label. That probe unlocks the VoxCPM2/Hindi-model route (arms C/I/J) and
already missed its own gate (CER 0.28-0.31, docs/URDU_BAKEOFF_RESULTS.md SS8).
This probe answers a different, never-tested question: OmniVoice
(`omnivoice_urdu` in backend/app/inference/catalog.py) only claims
`(ur, Script.ARABIC)` -- it has no Devanagari path -- so the conversion that
would actually let a user type Roman and reach OmniVoice is Roman ->
Perso-Arabic, not Roman -> Devanagari. Nobody has tried that direction yet.

No new gold-authoring needed: `eval/fixtures/urdu_corpus.json`'s own
`_meta.authoring_rule` states `perso_arabic` was authored FIRST as the source
of truth, and `roman` is a hand-authored rendering of the SAME sentence. Every
item already has a matched (roman, perso_arabic) gold pair.

PHASE A2 (2026-08-16): FOUR ARMS AND THREE METRICS
---------------------------------------------------
The plan requires establishing a proper baseline with the EXISTING Qwen
infrastructure before any new model is surveyed, so this script grew rather
than being replaced. Four arms in one model load:

  control_zero_shot  the original prompt, no examples          <- the control
  control_few_shot   the original prompt + its original 2 examples
  strict_zero_shot   the transliteration-only prompt
  strict_few_shot    the transliteration-only prompt + 6 richer examples

`control_*` are held byte-identical to the arms that produced
docs/URDU_BAKEOFF_RESULTS.md SS8b, so any improvement is attributable to the
prompt rather than to a corpus that has meanwhile grown from 13 items to 45.

Three metrics, because CER alone cannot express the output contract (see
`eval/translit_metrics.py` for the full argument):

  CER                       breakage screen ONLY -- explicitly not the verdict
  code-switch preservation  do the gold's Latin tokens survive verbatim?
  conversion completeness   did Urdu content actually BECOME Perso-Arabic?

The third is not "does some Arabic appear" -- that test passes
`میں office ja raha hoon`, which is useless output. It subtracts the gold's
Latin tokens from the output's, and whatever remains is unconverted Urdu.

WHAT THIS SCRIPT DOES NOT DECIDE
---------------------------------
Nothing, same discipline as run_translit_probe.py. It generates conversions and
records numbers. Per the plan, the real gate is A3: the top candidates are
synthesized through the actual OmniVoiceBackend and judged by ear. A model that
wins every column here has still not earned anything.

Run on the pod, in the existing .venv-qwen (same one run_translit_probe.py and
run_qwen_analyzer_probe.py use -- no new provisioning needed):

    backend/.venv-qwen/bin/python eval/run_roman_arabic_probe.py

Escalation to a bigger model (e.g. after the 3B run misses the gate) reuses
this same script and venv -- transformers/torch don't care which checkpoint
they load -- via PROBE_MODEL_ID:

    PROBE_MODEL_ID=Qwen/Qwen2.5-7B-Instruct \
        backend/.venv-qwen/bin/python eval/run_roman_arabic_probe.py
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from eval_harness import compute_cer  # noqa: E402
from translit_metrics import score_contract  # noqa: E402
from urdu_represent import CorpusItem, load_corpus  # noqa: E402

from app.domain.language import Script, detect_script  # noqa: E402

# Overridable via PROBE_MODEL_ID so the 7B escalation (see
# docs/URDU_BAKEOFF_RESULTS.md SS8b's closing note) reuses this exact
# scaffolding rather than forking a near-duplicate script. Unpinned on
# purpose for this probe only -- golden rule 7 applies to what SHIPS, not a
# one-off capability check. Same reasoning as the two Qwen probes this one
# is a sibling of.
MODEL_ID = os.environ.get("PROBE_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct")

# Model-specific output dir so a bigger-model rerun doesn't clobber the
# smaller model's manifest -- both are findings worth keeping side by side.
_MODEL_SLUG = re.sub(r"[^a-zA-Z0-9]+", "_", MODEL_ID).strip("_").lower()
OUT_DIR = _REPO_ROOT / "eval" / "results" / f"roman_arabic_probe_{_MODEL_SLUG}"

#: The original two exemplars. Frozen -- `control_few_shot` must stay
#: byte-identical to the arm that produced SS8b's numbers.
_CONTROL_EXEMPLAR_IDS = ("technical", "colloquial")

#: Richer set for the strict arm, chosen to demonstrate the contract's hard
#: cases rather than to show more of the same:
#:   technical             code-switch kept in Latin
#:   colloquial            pure Urdu, no Latin at all
#:   cs_02_meeting         sentence STARTS with an English word -- catches a
#:                         model that picks the output script from token 1
#:   spell_05_dropped_vowels  SMS orthography ("mjhe smjh nhi aa rha k")
#:   messy_03_abbrev       chat abbreviations survive verbatim, not expanded
#:   name_03_institution   the mixed decision: person's name converts, the
#:                         institution's name stays Latin, in one sentence
_STRICT_EXEMPLAR_IDS = (
    "technical",
    "colloquial",
    "cs_02_meeting",
    "spell_05_dropped_vowels",
    "messy_03_abbrev",
    "name_03_institution",
)

_SYSTEM_PROMPT_ZERO_SHOT = """You are a script-transliteration engine for Urdu. Convert the given \
Roman-Urdu (Latin-script) sentence into Perso-Arabic Urdu script, preserving the exact words, \
meaning, and word order -- this is a script change, not a translation or paraphrase. Leave any \
English words already in Latin script unchanged -- do not transliterate them into Urdu script.

Return ONLY the transliterated sentence. No explanation, no notes, no quotation marks, nothing \
else."""

# The strict prompt states the contract as numbered non-negotiables rather than
# as prose. SS8/SS8b's catalogued failures were instruction-following, not
# knowledge: translating code-switched English (آفس -> کامگاہ), converting only
# part of the sentence, and answering in Latin. Each rule below names one of
# those observed failures instead of describing the task in general terms.
_SYSTEM_PROMPT_STRICT = """You convert Roman Urdu into Perso-Arabic Urdu script. \
You change the SCRIPT ONLY. You never change the words.

Rules, in order of importance:

1. Write Urdu words in Perso-Arabic script. Every Urdu word must be converted -- \
never leave part of the sentence in Latin letters.
2. English words stay EXACTLY as they are, in Latin letters, character for character. \
Do not translate them. Do not convert them to Urdu script. Do not change their capitalisation. \
"office" stays "office", never "دفتر". "GitHub" stays "GitHub", never "github".
3. Do not translate, explain, summarise, correct, or improve anything. The output must be the \
same sentence the user wrote, in a different script.
4. Keep the user's own wording, tone and word order, including informal or misspelled words. \
Do not add or remove punctuation the user did not write.
5. Names of people and places are Urdu words -- convert them. Brand, product and company names \
written in Latin are English -- leave them.

Output the converted sentence and nothing else. No preamble, no notes, no quotation marks."""


def _fewshot(prompt: str, corpus: tuple[CorpusItem, ...], ids: tuple[str, ...]) -> str:
    by_id = {i.id: i for i in corpus}
    block = "\n\n".join(
        f"Roman: {by_id[i].roman}\nUrdu: {by_id[i].perso_arabic}" for i in ids if i in by_id
    )
    return prompt + "\n\nExamples:\n" + block


@dataclass(frozen=True, slots=True)
class Arm:
    """One (prompt, exemplar-set) combination, scored independently."""

    name: str
    prompt_kind: str          # "control" | "strict"
    exemplar_ids: tuple[str, ...]   # empty for zero-shot

    def system_prompt(self, corpus: tuple[CorpusItem, ...]) -> str:
        base = (
            _SYSTEM_PROMPT_ZERO_SHOT if self.prompt_kind == "control"
            else _SYSTEM_PROMPT_STRICT
        )
        return _fewshot(base, corpus, self.exemplar_ids) if self.exemplar_ids else base


ARMS: tuple[Arm, ...] = (
    Arm("control_zero_shot", "control", ()),
    Arm("control_few_shot", "control", _CONTROL_EXEMPLAR_IDS),
    Arm("strict_zero_shot", "strict", ()),
    Arm("strict_few_shot", "strict", _STRICT_EXEMPLAR_IDS),
)


def build_prompt(roman_text: str) -> str:
    return f"Transliterate this Roman-Urdu sentence into Perso-Arabic Urdu script:\n{roman_text}"


def parse_transliteration(raw: str) -> str:
    """
    Returns the cleaned response, or raises. Same discipline as
    run_translit_probe.py's function of the same name: an empty response or
    one carrying zero Perso-Arabic (Arabic-script) characters is not a
    transliteration at all, never silently scored as a bad one.
    """
    text = raw.strip().strip('"').strip("'").strip()
    if not text:
        raise RuntimeError(f"empty transliteration response: {raw!r}")
    _, ratios = detect_script(text)
    if ratios.get(Script.ARABIC, 0.0) <= 0.0:
        raise RuntimeError(f"response contains no Arabic-script characters: {raw!r}")
    return text


@dataclass(frozen=True, slots=True)
class ProbeCase:
    item_id: str
    category: str
    variant: str  # the Arm name
    source_roman: str
    gold_perso_arabic: str


def build_cases(corpus: tuple[CorpusItem, ...]) -> list[ProbeCase]:
    """
    Every (item, arm) pair, minus each arm's own exemplars.

    An arm is never scored on a sentence it was shown. That exclusion is
    per-arm, not global: `spell_05_dropped_vowels` is an exemplar for
    `strict_few_shot` only, so the other three arms still score it -- dropping
    it everywhere would shrink the shared corpus to whatever the most
    example-hungry arm happened to need.
    """
    return [
        ProbeCase(
            item_id=item.id, category=item.category, variant=arm.name,
            source_roman=item.roman, gold_perso_arabic=item.perso_arabic,
        )
        for arm in ARMS
        for item in corpus
        if item.id not in arm.exemplar_ids
    ]


@dataclass
class ItemResult:
    item_id: str
    category: str
    variant: str
    source_roman: str
    gold_perso_arabic: str
    status: str = "ok"  # ok | unparseable
    raw_output: str = ""
    parsed_output: str = ""
    cer: float | None = None
    # -- contract metrics; see eval/translit_metrics.py
    code_switch_preservation: float | None = None
    conversion_completeness: float | None = None
    contract_ok: bool | None = None
    lost_code_switch: list[str] = field(default_factory=list)
    recased_code_switch: list[str] = field(default_factory=list)
    unconverted_residue: list[str] = field(default_factory=list)
    arabic_share_output: float | None = None
    arabic_share_gold: float | None = None
    gen_time_sec: float | None = None
    error: str = ""


@dataclass
class RunMeta:
    model_id: str
    item_count: int
    arms: list[dict] = field(
        default_factory=lambda: [
            {"name": a.name, "prompt_kind": a.prompt_kind,
             "exemplar_ids": list(a.exemplar_ids)}
            for a in ARMS
        ]
    )
    load_time_sec: float | None = None
    python: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def summarize(results: list[ItemResult]) -> dict:
    """Per-arm and per-category metrics, plus owner_core called out individually."""

    def mean_of(rows: list[ItemResult], attr: str) -> float | None:
        scored = [
            getattr(r, attr) for r in rows
            if r.status == "ok" and getattr(r, attr) is not None
        ]
        return round(sum(scored) / len(scored), 4) if scored else None

    def mean_cer(rows: list[ItemResult]) -> float | None:
        return mean_of(rows, "cer")

    variants = [a.name for a in ARMS if any(r.variant == a.name for r in results)]
    by_variant = {}
    for variant in variants:
        rows = [r for r in results if r.variant == variant]
        ok_rows = [r for r in rows if r.status == "ok"]
        by_variant[variant] = {
            # CER first only because it is the familiar number. The two
            # contract metrics below it are the ones that match the spec.
            "mean_cer": mean_cer(rows),
            "mean_code_switch_preservation": mean_of(rows, "code_switch_preservation"),
            "mean_conversion_completeness": mean_of(rows, "conversion_completeness"),
            # The headline: how many outputs satisfied the contract outright.
            "contract_ok": sum(1 for r in ok_rows if r.contract_ok),
            "scored": len(ok_rows),
            "ok": len(ok_rows),
            "unparseable": sum(1 for r in rows if r.status != "ok"),
            # Which failure mode dominates -- translation vs half-conversion.
            "items_with_lost_code_switch": sum(1 for r in ok_rows if r.lost_code_switch),
            "items_with_residue": sum(1 for r in ok_rows if r.unconverted_residue),
            "items_with_recasing": sum(1 for r in ok_rows if r.recased_code_switch),
        }

    categories = sorted({r.category for r in results})
    by_category = {
        category: {
            variant: mean_cer(
                [r for r in results if r.category == category and r.variant == variant]
            )
            for variant in variants
        }
        for category in categories
    }

    owner_core_items = [
        {
            "item_id": r.item_id, "variant": r.variant, "status": r.status,
            "cer": r.cer, "contract_ok": r.contract_ok,
            "lost_code_switch": r.lost_code_switch,
            "unconverted_residue": r.unconverted_residue,
        }
        for r in results
        if r.category == "owner_core"
    ]

    return {
        "by_variant": by_variant,
        "by_category": by_category,
        "owner_core_items": owner_core_items,
    }


def main() -> int:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    corpus = load_corpus()
    cases = build_cases(corpus)
    prompts = {arm.name: arm.system_prompt(corpus) for arm in ARMS}

    print(f"Loading {MODEL_ID}...")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, dtype=torch.bfloat16, device_map="cuda"
    )
    load_time_sec = time.time() - t0
    print(f"Loaded in {load_time_sec:.1f}s")

    results: list[ItemResult] = []
    for case in cases:
        messages = [
            {"role": "system", "content": prompts[case.variant]},
            {"role": "user", "content": build_prompt(case.source_roman)},
        ]
        inputs = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        ).to(model.device)

        t1 = time.time()
        out = model.generate(**inputs, max_new_tokens=256, do_sample=False)
        gen_sec = time.time() - t1
        raw = tokenizer.decode(
            out[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )

        row = ItemResult(
            item_id=case.item_id, category=case.category, variant=case.variant,
            source_roman=case.source_roman, gold_perso_arabic=case.gold_perso_arabic,
            raw_output=raw, gen_time_sec=gen_sec,
        )
        try:
            parsed = parse_transliteration(raw)
            row.parsed_output = parsed
            row.cer = compute_cer(case.gold_perso_arabic, parsed, "ur")

            contract = score_contract(parsed, case.gold_perso_arabic)
            row.code_switch_preservation = contract.code_switch_preservation
            row.conversion_completeness = contract.conversion_completeness
            row.contract_ok = contract.contract_ok
            row.lost_code_switch = contract.lost_code_switch
            row.recased_code_switch = contract.recased_code_switch
            row.unconverted_residue = contract.unconverted_residue
            row.arabic_share_output = contract.arabic_share_output
            row.arabic_share_gold = contract.arabic_share_gold

            flag = "OK " if contract.contract_ok else "   "
            note = ""
            if contract.lost_code_switch:
                note += f" lost={contract.lost_code_switch}"
            if contract.unconverted_residue:
                note += f" residue={contract.unconverted_residue[:4]}"
            print(
                f"{flag}[{case.item_id}/{case.variant}] CER {row.cer:.4f} "
                f"pres {contract.code_switch_preservation:.2f} "
                f"compl {contract.conversion_completeness:.2f}{note}"
            )
        except RuntimeError as exc:
            row.status, row.error = "unparseable", str(exc)
            print(f"   [{case.item_id}/{case.variant}] UNPARSEABLE: {exc}")
        results.append(row)

    summary = summarize(results)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": asdict(
            RunMeta(model_id=MODEL_ID, item_count=len(cases), load_time_sec=load_time_sec)
        ),
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n{'=' * 78}")
    print(f"Wrote {OUT_DIR / 'manifest.json'}\n")
    print(f"{'arm':<20} {'contract':<10} {'CER':<8} {'preserve':<9} {'complete':<9} unparse")
    print("-" * 78)
    for variant, s in summary["by_variant"].items():
        def fmt(key: str) -> str:
            v = s[key]
            return f"{v:.4f}" if v is not None else "n/a"

        contract = f"{s['contract_ok']}/{s['scored']}"
        print(
            f"{variant:<20} {contract:<10} {fmt('mean_cer'):<8} "
            f"{fmt('mean_code_switch_preservation'):<9} "
            f"{fmt('mean_conversion_completeness'):<9} {s['unparseable']}"
        )

    print("\nfailure mode breakdown (items affected):")
    for variant, s in summary["by_variant"].items():
        print(
            f"  {variant:<20} translated-away={s['items_with_lost_code_switch']:<3} "
            f"left-in-latin={s['items_with_residue']:<3} "
            f"recased={s['items_with_recasing']}"
        )

    print("\nowner_core items (highest-priority slice -- the owner's own 5 sentences):")
    for row in summary["owner_core_items"]:
        cer = f"{row['cer']:.4f}" if row["cer"] is not None else row["status"]
        mark = "OK " if row["contract_ok"] else "   "
        print(f"  {mark}[{row['item_id']}/{row['variant']}] CER {cer}")

    print(
        "\n'contract' is the headline: outputs where the gold's Latin survived verbatim AND\n"
        "no Urdu was left unconverted. CER is a breakage screen only -- it cannot see a\n"
        "translated code-switch word, and it rates a half-converted sentence generously.\n"
        "\nThis script reports numbers. It decides nothing: per the plan the real gate is A3,\n"
        "where the top candidates are synthesized through OmniVoice and judged by ear."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
