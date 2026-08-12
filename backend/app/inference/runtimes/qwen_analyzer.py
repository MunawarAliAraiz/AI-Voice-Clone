"""
AI Voice Clone Studio — Qwen2.5-Instruct Speech Direction analyzer runtime.

Torch lives here (allowed: under `inference/runtimes/`). Ports the validated
prompt/parsing logic from `eval/run_qwen_analyzer_probe.py` (0 validation
problems across 6 English / Roman-Urdu / Hindi test cases run on the pod
against `Qwen/Qwen2.5-3B-Instruct`, unpinned `main`, before this file
existed) into the same load/classify/unload shape the audio runtimes follow
— minus `synth`, because there is no audio anywhere in this path.

DESIGN DECISION THIS FILE DOES NOT RENEGOTIATE: segmentation, sentence text,
and emphasis spans are decided entirely by the deterministic heuristic
(`domain.direction_analyze.analyze`) before a sentence ever reaches this
backend — see that module's docstring and `inference/analyzer_scheduler.py`'s.
`classify()`'s only job is labeling emotion/intensity/energy/rate for the
sentences it is handed, in the order it is handed them. It never re-segments
`language`/text and never invents a sentence that was not in its input.

GOLDEN RULE 5 (no silent fallback), CONCRETELY: `classify()` raises a plain
`RuntimeError` on any response that is not valid JSON, is not a list, is the
wrong length, is missing a row's `index`, or contains an `emotion`/
`intensity`/`energy`/`rate` value outside `Emotion`/`Level`/`Rate` — never a
best-effort partial result, never a substituted default classification. The
worker's existing exception-to-WireResponse mapping (`inference/worker.py`)
turns that raise into a non-ok response with `error_code`/`error_message`;
this file does not decide what happens next, the scheduler does.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

from ...domain.direction import Emotion, Level, Rate

__all__ = ["QwenAnalyzerBackend"]

_VALID_EMOTIONS = tuple(e.value for e in Emotion)
_VALID_LEVELS = tuple(level.value for level in Level)
_VALID_RATES = tuple(r.value for r in Rate)

#: Ported verbatim (module docstrings updated for context) from the probe —
#: see eval/run_qwen_analyzer_probe.py for the R1-style validation this
#: prompt was checked against before any of this plumbing was built.
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

#: Probe default; generous enough for a full sentence list's worth of JSON
#: rows without giving the model room to ramble past the array.
_DEFAULT_MAX_NEW_TOKENS = 300


def _build_prompt(sentences: list[str]) -> str:
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    return f"Sentences:\n{numbered}"


def _parse_and_validate(raw: str, expected_count: int) -> list[dict[str, Any]]:
    """
    Parse+validate exactly what the probe's `validate_response` reported as
    `problems` — except here a problem is fatal, not a line in a report.
    """
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError(f"no JSON array found in analyzer response: {raw!r}")
    try:
        rows = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"analyzer response JSON parse failed: {exc}") from exc

    if not isinstance(rows, list):
        raise RuntimeError("analyzer response is not a JSON array")
    if len(rows) != expected_count:
        raise RuntimeError(
            f"analyzer response has {len(rows)} rows, expected {expected_count}"
        )

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError(f"analyzer response row {i} is not an object")
        if "index" not in row:
            raise RuntimeError(f"analyzer response row {i} is missing 'index'")
        for field, valid in (
            ("emotion", _VALID_EMOTIONS), ("intensity", _VALID_LEVELS),
            ("energy", _VALID_LEVELS), ("rate", _VALID_RATES),
        ):
            if row.get(field) not in valid:
                raise RuntimeError(
                    f"analyzer response row {i}: {field}={row.get(field)!r} not in {valid}"
                )
    return rows


class QwenAnalyzerBackend:
    """
    One Qwen2.5-Instruct process. Classification only — no `synth`, because
    there is no audio in this path (see the module docstring).
    """

    runtime = "qwen_analyzer"

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self.loaded_model_id: str | None = None

    def load(self, model_id: str, hf_repo: str, hf_revision: str) -> float:
        t0 = time.time()
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForCausalLM, AutoTokenizer

        # Honour the pinned revision (golden rule 7): resolve the exact
        # snapshot on disk and load from that path, rather than trusting
        # `main`. The probe ran unpinned deliberately (a one-off capability
        # check, not what ships) — this is the production path and pins.
        model_path = snapshot_download(repo_id=hf_repo, revision=hf_revision)
        self._tokenizer = AutoTokenizer.from_pretrained(model_path)
        self._model = AutoModelForCausalLM.from_pretrained(
            model_path, dtype=torch.bfloat16, device_map="cuda"
        )
        self.loaded_model_id = model_id
        return time.time() - t0

    def classify(
        self, *, language: str, sentences: list[str], params: dict[str, Any]
    ) -> dict[str, Any]:
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("classify called before load")
        if not sentences:
            return {"rows": [], "gen_time_sec": 0.0}

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(sentences)},
        ]
        # return_dict=True explicitly: the probe hit and fixed a real bug
        # here — on this transformers version, apply_chat_template's return
        # shape without it is not interchangeable with what
        # model.generate(**inputs) expects; passing it positionally instead
        # fails deep inside generate() with an opaque AttributeError on
        # `.shape`. Do not regress this.
        inputs = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True,
        ).to(self._model.device)

        max_new_tokens = int(params.get("max_new_tokens", _DEFAULT_MAX_NEW_TOKENS))
        t0 = time.time()
        out = self._model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        gen_sec = time.time() - t0
        response = self._tokenizer.decode(
            out[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )

        rows = _parse_and_validate(response, len(sentences))
        return {"rows": rows, "gen_time_sec": gen_sec}

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self.loaded_model_id = None
        with contextlib.suppress(Exception):
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
