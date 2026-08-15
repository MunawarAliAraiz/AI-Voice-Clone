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

Two prompt variants scored in one pod run (one model load):
  - zero_shot: system prompt describes the task, no examples.
  - few_shot:  same prompt plus 2 worked examples pulled from the `technical`
               and `colloquial` corpus items -- excluded from few_shot scoring
               (but still scored under zero_shot) to avoid leakage.

WHAT THIS SCRIPT DOES NOT DECIDE
---------------------------------
Nothing, same discipline as run_translit_probe.py. Generates transliterations,
records CER against gold, no verdict. A human reads the manifest.

Run on the pod, in the existing .venv-qwen (same one run_translit_probe.py and
run_qwen_analyzer_probe.py use -- no new provisioning needed):

    backend/.venv-qwen/bin/python eval/run_roman_arabic_probe.py
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from eval_harness import compute_cer  # noqa: E402
from urdu_represent import CorpusItem, load_corpus  # noqa: E402

from app.domain.language import Script, detect_script  # noqa: E402

MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"
# Unpinned on purpose for this probe only -- golden rule 7 applies to what
# SHIPS, not a one-off capability check. Same reasoning as the two Qwen
# probes this one is a sibling of.

OUT_DIR = _REPO_ROOT / "eval" / "results" / "roman_arabic_probe"

#: Held out as few-shot exemplars; excluded from few_shot scoring so the
#: model is never scored on an example it was shown.
_FEWSHOT_EXEMPLAR_IDS = {"technical", "colloquial"}

_SYSTEM_PROMPT_ZERO_SHOT = """You are a script-transliteration engine for Urdu. Convert the given \
Roman-Urdu (Latin-script) sentence into Perso-Arabic Urdu script, preserving the exact words, \
meaning, and word order -- this is a script change, not a translation or paraphrase. Leave any \
English words already in Latin script unchanged -- do not transliterate them into Urdu script.

Return ONLY the transliterated sentence. No explanation, no notes, no quotation marks, nothing \
else."""

_SYSTEM_PROMPT_FEW_SHOT = _SYSTEM_PROMPT_ZERO_SHOT + "\n\nExamples:\n{examples}"


def _build_fewshot_block(corpus: tuple[CorpusItem, ...]) -> str:
    exemplars = [i for i in corpus if i.id in _FEWSHOT_EXEMPLAR_IDS]
    return "\n".join(f'Roman: {i.roman}\nUrdu: {i.perso_arabic}' for i in exemplars)


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
    variant: str  # "zero_shot" | "few_shot"
    source_roman: str
    gold_perso_arabic: str


def build_cases(corpus: tuple[CorpusItem, ...]) -> list[ProbeCase]:
    cases = [
        ProbeCase(
            item_id=item.id, category=item.category, variant="zero_shot",
            source_roman=item.roman, gold_perso_arabic=item.perso_arabic,
        )
        for item in corpus
    ]
    cases += [
        ProbeCase(
            item_id=item.id, category=item.category, variant="few_shot",
            source_roman=item.roman, gold_perso_arabic=item.perso_arabic,
        )
        for item in corpus
        if item.id not in _FEWSHOT_EXEMPLAR_IDS
    ]
    return cases


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
    gen_time_sec: float | None = None
    error: str = ""


@dataclass
class RunMeta:
    model_id: str
    item_count: int
    fewshot_exemplar_ids: list[str] = field(default_factory=lambda: sorted(_FEWSHOT_EXEMPLAR_IDS))
    load_time_sec: float | None = None
    python: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def summarize(results: list[ItemResult]) -> dict:
    """Per-variant and per-category mean CER, plus owner_core called out individually."""

    def mean_cer(rows: list[ItemResult]) -> float | None:
        scored = [r.cer for r in rows if r.status == "ok" and r.cer is not None]
        return sum(scored) / len(scored) if scored else None

    variants = sorted({r.variant for r in results})
    by_variant = {
        variant: {
            "mean_cer": mean_cer(rows := [r for r in results if r.variant == variant]),
            "ok": sum(1 for r in rows if r.status == "ok"),
            "unparseable": sum(1 for r in rows if r.status != "ok"),
        }
        for variant in variants
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
        {"item_id": r.item_id, "variant": r.variant, "status": r.status, "cer": r.cer}
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
    fewshot_block = _build_fewshot_block(corpus)

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
        system_prompt = (
            _SYSTEM_PROMPT_ZERO_SHOT if case.variant == "zero_shot"
            else _SYSTEM_PROMPT_FEW_SHOT.format(examples=fewshot_block)
        )
        messages = [
            {"role": "system", "content": system_prompt},
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
            print(f"[{case.item_id}/{case.variant}] CER {row.cer:.4f}  ({gen_sec:.2f}s)")
        except RuntimeError as exc:
            row.status, row.error = "unparseable", str(exc)
            print(f"[{case.item_id}/{case.variant}] UNPARSEABLE: {exc}")
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

    print(f"\n{'=' * 60}")
    print(f"Wrote {OUT_DIR / 'manifest.json'}")
    for variant, stats in summary["by_variant"].items():
        mean = f"{stats['mean_cer']:.4f}" if stats["mean_cer"] is not None else "n/a"
        print(
            f"  {variant}: mean CER {mean}  "
            f"({stats['ok']} ok, {stats['unparseable']} unparseable)"
        )
    print("\nowner_core items (highest-priority slice -- the owner's own 5 sentences):")
    for row in summary["owner_core_items"]:
        cer = f"{row['cer']:.4f}" if row["cer"] is not None else row["status"]
        print(f"  [{row['item_id']}/{row['variant']}] {cer}")
    print("\nThis script reports numbers. It decides nothing -- see the module docstring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
