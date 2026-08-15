"""
Phase 2 — Urdu transliteration-viability capability probe (Qwen2.5-Instruct).

NOT a new feature, and NOT the transliteration layer itself. Same spirit as
`eval/run_qwen_analyzer_probe.py` (this file's structural template): an
R1-style capability check the project's own convention requires before real
plumbing gets built — does Qwen2.5-3B-Instruct convert Urdu text into
Devanagari well enough that a real `domain/urdu_text.py` transliteration layer
would be worth building at all?

Why this matters (docs/ROADMAP.md's Phase 2 remaining items; docs/HANDOFF.md's
design-facts section): `eval/fixtures/urdu_corpus.json`'s `devanagari` field is
HAND-AUTHORED GOLD, not the output of any converter — see that file's own
`_meta.why_devanagari_is_hand_authored`. Perso-Arabic is an abjad and omits
short vowels; Devanagari is an abugida and must write them, so a real converter
needs a vowel-restoration step a bad implementation would get wrong in a way
that's indistinguishable from "the TTS model can't speak Urdu from Devanagari"
— hand-authoring the gold removed that variable from the bake-off. Arms C/I/J
there were a CEILING TEST on what any converter could deliver, and the blind
listen cleared it: Devanagari input (arm C) beat Roman/Perso-Arabic VoxCPM2 by
a full point on pronunciation and naturalness. That's the gate this probe was
waiting behind (docs/HANDOFF.md: "Phase 2 ... gated on clearing the bake-off's
hand-authored gold Devanagari").

A failure mode is already on record for the NAIVE approach this probe is
measuring against: a rule-based Roman->Devanagari attempt measured
مجھے -> "majhay" -> मझे instead of मुझे — a wrong vowel (see
backend/app/domain/routing.py's `_plan_transform`, the `TransformKind.ARAB_TO_DEVA`
branch). This probe checks whether an LLM does the vowel-restoration step
better than that.

Two directions are scored per corpus item: `perso_arabic` -> Devanagari and
`roman` -> Devanagari, each against the item's hand-authored `devanagari` gold,
via `eval/eval_harness.py`'s `compute_cer` (jiwer-based, script-aware
normalization — reused, not reimplemented).

WHAT THIS SCRIPT DOES NOT DECIDE
---------------------------------
Nothing. It generates transliterations and records CER numbers, per item and
aggregated. No `verified=True`, no pass/fail verdict, no recommendation — same
discipline as `run_urdu_bakeoff.py`'s own docstring. A human reads the
manifest.

Run on the pod, under a dedicated venv (own torch/transformers pin, same
per-runtime-interpreter reasoning as every other runtime in this project — this
can share `.venv-qwen` with run_qwen_analyzer_probe.py, plus `jiwer` for CER):

    uv venv backend/.venv-qwen --python 3.12
    uv pip install --python backend/.venv-qwen \
        torch --index-url https://download.pytorch.org/whl/cu128
    uv pip install --python backend/.venv-qwen transformers accelerate jiwer
    backend/.venv-qwen/bin/python eval/run_translit_probe.py
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
# Unpinned on purpose for this probe only — golden rule 7 (pin every HF
# revision) applies to what SHIPS, not to a one-off capability check. Same
# reasoning as run_qwen_analyzer_probe.py's MODEL_ID.

OUT_DIR = _REPO_ROOT / "eval" / "results" / "translit_probe"

_SYSTEM_PROMPT = """You are a script-transliteration engine for Urdu. Convert the given \
sentence into Devanagari script, preserving the exact words, meaning, and word order — this \
is a script change, not a translation or paraphrase. Where the source omits short vowels \
(Perso-Arabic script does; Roman spelling can too), restore them the way Hindi-Urdu \
(Hindustani) is conventionally written in Devanagari. Preserve Urdu-specific phonemes with \
nuqta marks where they apply (फ़ ज़ क़ ग़ ख़). Leave any English words already in Latin script \
unchanged — do not transliterate them into Devanagari.

Return ONLY the transliterated sentence. No explanation, no notes, no quotation marks, \
nothing else."""

#: Corpus field name -> human-readable label for the direction, used in both
#: the user prompt and every result row's `direction`.
_DIRECTIONS: dict[str, str] = {
    "perso_arabic": "Perso-Arabic Urdu",
    "roman": "Roman Urdu (Latin script)",
}


def build_prompt(source_field: str, text: str) -> str:
    return f"Transliterate this {_DIRECTIONS[source_field]} sentence into Devanagari:\n{text}"


def parse_transliteration(raw: str) -> str:
    """
    Returns the cleaned response, or raises. Golden rule 5, concretely, for a
    free-text (not JSON) response: an empty response or one carrying zero
    Devanagari characters is not a transliteration at all, so it is never
    silently scored as a bad one — see `qwen_analyzer.py`'s `classify()` for
    the same discipline applied to the JSON-classification probe.

    A wrapping quote is stripped rather than flagged: it's formatting noise the
    system prompt asked the model not to add, not evidence the transliteration
    itself is wrong.
    """
    text = raw.strip().strip('"').strip("'").strip()
    if not text:
        raise RuntimeError(f"empty transliteration response: {raw!r}")
    _, ratios = detect_script(text)
    if ratios.get(Script.DEVANAGARI, 0.0) <= 0.0:
        raise RuntimeError(f"response contains no Devanagari characters: {raw!r}")
    return text


@dataclass(frozen=True, slots=True)
class ProbeCase:
    item_id: str
    category: str
    direction: str  # corpus field transliterated FROM: "perso_arabic" | "roman"
    source_text: str
    gold_devanagari: str


def build_cases(corpus: tuple[CorpusItem, ...]) -> list[ProbeCase]:
    return [
        ProbeCase(
            item_id=item.id, category=item.category, direction=direction,
            source_text=getattr(item, direction), gold_devanagari=item.devanagari,
        )
        for item in corpus
        for direction in _DIRECTIONS
    ]


@dataclass
class ItemResult:
    item_id: str
    category: str
    direction: str
    source_text: str
    gold_devanagari: str
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
    load_time_sec: float | None = None
    python: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def summarize(results: list[ItemResult]) -> dict:
    """
    Per-direction and per-category mean CER, plus the `owner_core` items called
    out individually rather than folded into an aggregate — those are the
    roadmap's highest-priority slice (the owner's own 5 sentences). Numbers
    only; no verdict, per the module docstring.
    """

    def mean_cer(rows: list[ItemResult]) -> float | None:
        scored = [r.cer for r in rows if r.status == "ok" and r.cer is not None]
        return sum(scored) / len(scored) if scored else None

    by_direction = {
        direction: {
            "mean_cer": mean_cer(rows := [r for r in results if r.direction == direction]),
            "ok": sum(1 for r in rows if r.status == "ok"),
            "unparseable": sum(1 for r in rows if r.status != "ok"),
        }
        for direction in _DIRECTIONS
    }

    categories = sorted({r.category for r in results})
    by_category = {
        category: {
            direction: mean_cer(
                [r for r in results if r.category == category and r.direction == direction]
            )
            for direction in _DIRECTIONS
        }
        for category in categories
    }

    owner_core_items = [
        {"item_id": r.item_id, "direction": r.direction, "status": r.status, "cer": r.cer}
        for r in results
        if r.category == "owner_core"
    ]

    return {
        "by_direction": by_direction,
        "by_category": by_category,
        "owner_core_items": owner_core_items,
    }


def main() -> int:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    corpus = load_corpus()
    cases = build_cases(corpus)

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
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(case.direction, case.source_text)},
        ]
        # return_dict=True explicitly — see run_qwen_analyzer_probe.py's build_prompt
        # neighbor comment: newer transformers changed apply_chat_template's default
        # return shape, and model.generate() does not accept the two forms
        # interchangeably.
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
            item_id=case.item_id, category=case.category, direction=case.direction,
            source_text=case.source_text, gold_devanagari=case.gold_devanagari,
            raw_output=raw, gen_time_sec=gen_sec,
        )
        try:
            parsed = parse_transliteration(raw)
            row.parsed_output = parsed
            row.cer = compute_cer(case.gold_devanagari, parsed, "hi")
            print(f"[{case.item_id}/{case.direction}] CER {row.cer:.4f}  ({gen_sec:.2f}s)")
        except RuntimeError as exc:
            row.status, row.error = "unparseable", str(exc)
            print(f"[{case.item_id}/{case.direction}] UNPARSEABLE: {exc}")
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
    for direction, stats in summary["by_direction"].items():
        mean = f"{stats['mean_cer']:.4f}" if stats["mean_cer"] is not None else "n/a"
        print(
            f"  {direction}: mean CER {mean}  "
            f"({stats['ok']} ok, {stats['unparseable']} unparseable)"
        )
    print("\nowner_core items (highest-priority slice — the owner's own 5 sentences):")
    for row in summary["owner_core_items"]:
        cer = f"{row['cer']:.4f}" if row["cer"] is not None else row["status"]
        print(f"  [{row['item_id']}/{row['direction']}] {cer}")
    print("\nThis script reports numbers. It decides nothing — see the module docstring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
