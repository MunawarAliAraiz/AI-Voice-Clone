"""
Phase 2 remainder — Qwen2.5-Instruct LLM analyzer, capability probe.

NOT the production analyzer. This is the R1-style validation step the
project's own convention requires before building real plumbing (see how
Phase 4's ChatterboxBackend followed a design doc + capability check, and
Phase A validated every model before routing to it): does Qwen2.5-Instruct
reliably produce valid, parseable direction classifications at all, before
any worker process / job kind / wire protocol gets built around it?

Design decision this probe tests: segmentation, emphasis spans, and
pause_after_ms stay on the existing deterministic heuristic
(`domain.text.split_sentences` + `direction_analyze.py`'s punctuation rules)
— those are already correct and free. The LLM is asked to do ONLY the one
thing a keyword lexicon is structurally bad at: classify emotion / intensity
/ energy / rate for an already-segmented sentence, given surrounding context.
This keeps the prompt small, keeps segment boundaries and character offsets
(which LLMs are unreliable at counting) entirely off the model's plate, and
means a malformed model response can only produce a wrong classification —
never a broken segment.

Run on the pod, under a dedicated venv (own torch/transformers pin, same
per-runtime-interpreter reasoning as every other runtime in this project):

    uv venv backend/.venv-qwen --python 3.12
    uv pip install --python backend/.venv-qwen \
        torch --index-url https://download.pytorch.org/whl/cu128
    uv pip install --python backend/.venv-qwen transformers accelerate
    backend/.venv-qwen/bin/python eval/run_qwen_analyzer_probe.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.domain.direction import Emotion, Level, Rate  # noqa: E402
from app.domain.language import Script, detect_script  # noqa: E402
from app.domain.text import split_sentences  # noqa: E402

MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"
# Unpinned on purpose for this probe only — golden rule 7 (pin every HF
# revision) applies to what SHIPS, not to a one-off capability check. The
# production QwenAnalyzerBackend must pin a real commit sha once this probe
# says the model/size is worth building around.

_VALID_EMOTIONS = tuple(e.value for e in Emotion)
_VALID_LEVELS = tuple(l.value for l in Level)
_VALID_RATES = tuple(r.value for r in Rate)

_SYSTEM_PROMPT = f"""You are a speech-direction classifier for a text-to-speech system. \
For each numbered sentence, classify how it should be SPOKEN (not what it means literally).

Return ONLY a JSON array, one object per sentence, in order, no other text:
[{{"index": 0, "emotion": "...", "intensity": "...", "energy": "...", "rate": "..."}}, ...]

emotion: one of {_VALID_EMOTIONS}
intensity: one of {_VALID_LEVELS} (how strongly the emotion is expressed)
energy: one of {_VALID_LEVELS} (vocal energy/loudness)
rate: one of {_VALID_RATES} (speaking speed)

Use "neutral"/"medium"/"medium"/"normal" when nothing in the text signals otherwise \
— never guess dramatically to seem clever."""


@dataclass(frozen=True)
class Case:
    language: str
    text: str


CASES = (
    Case("en", "I am so excited about this! We finally did it!"),
    Case("en", "I'm really worried about the exam tomorrow. I haven't studied enough."),
    Case("en", "The quarterly report is due on Friday. Please submit it by 5 PM."),
    Case("ur", "Yeh bohat hi khatarnak baat hai! Tum kya soch rahe ho?"),
    Case("hi", "Aaj mausam bahut accha hai. Chalo bahar ghoomne chalte hain."),
    Case("en", "Get out. Get out right now. I never want to see you again."),
)


def build_prompt(sentences: list[str]) -> str:
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    return f"Sentences:\n{numbered}"


def validate_response(raw: str, expected_count: int) -> tuple[list[dict], list[str]]:
    """Returns (parsed_rows, problems). Never raises — a probe reports, not crashes."""
    problems: list[str] = []
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return [], [f"no JSON array found in response: {raw!r}"]
    try:
        rows = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        return [], [f"JSON parse failed: {e}"]

    if not isinstance(rows, list):
        return [], ["response is not a JSON array"]
    if len(rows) != expected_count:
        problems.append(f"expected {expected_count} rows, got {len(rows)}")

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"row {i}: not an object")
            continue
        for field, valid in (
            ("emotion", _VALID_EMOTIONS), ("intensity", _VALID_LEVELS),
            ("energy", _VALID_LEVELS), ("rate", _VALID_RATES),
        ):
            if row.get(field) not in valid:
                problems.append(f"row {i}: {field}={row.get(field)!r} not in {valid}")
    return rows, problems


def main() -> int:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"Loading {MODEL_ID}...")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, dtype=torch.bfloat16, device_map="cuda"
    )
    print(f"Loaded in {time.time() - t0:.1f}s")

    total_problems = 0
    for case in CASES:
        script, _ = detect_script(case.text)
        sentences = split_sentences(case.text, script)

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(sentences)},
        ]
        inputs = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        ).to(model.device)

        t0 = time.time()
        out = model.generate(inputs, max_new_tokens=300, do_sample=False)
        gen_sec = time.time() - t0
        response = tokenizer.decode(out[0][inputs.shape[1] :], skip_special_tokens=True)

        rows, problems = validate_response(response, len(sentences))
        total_problems += len(problems)

        print(f"\n=== [{case.language}] {case.text!r} ===")
        print(f"sentences: {sentences}")
        print(f"gen_time: {gen_sec:.2f}s")
        print(f"raw response: {response!r}")
        if problems:
            print(f"PROBLEMS: {problems}")
        else:
            print("VALID — classifications:")
            for row in rows:
                print(f"  [{row['index']}] {sentences[row['index']]!r} -> "
                      f"emotion={row['emotion']} intensity={row['intensity']} "
                      f"energy={row['energy']} rate={row['rate']}")

    print(f"\n{'=' * 60}\nTotal validation problems across {len(CASES)} cases: {total_problems}")
    print("A probe with zero problems is worth building a real worker around.")
    print("Any problem here is a prompt/model-capability issue to fix BEFORE writing")
    print("QwenAnalyzerBackend, not something to silently work around in production.")
    return 0 if total_problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
