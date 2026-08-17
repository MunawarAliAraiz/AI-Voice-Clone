"""
AI Voice Clone Studio — Gemma-4-31B Roman Urdu → Perso-Arabic transliterator.

Torch lives here (allowed: under `inference/runtimes/`). Same load/act/unload
shape as the audio runtimes and `qwen_analyzer.py`, minus `synth` — there is
no audio anywhere in this path.

WHY THIS MODEL, AND WHY NOT A SMALLER ONE
------------------------------------------
A3 ran three times against the same harness. Qwen2.5-7B → *"not usable"*
(CER 0.3061). Ministral-3-8B → ten owner-reported defects, nine of them valid
Urdu words meaning something ELSE (کال *call* for کل *tomorrow*, طباعت
*printing* for طبیعت *health*). **Gemma-4-31B at 4-bit → "perfect with the
current data… it's best"**, with all ten of Ministral's defects fixed.

Runs 2 and 3 scored the same contract rate to within one point and landed on
opposite sides of the gate. So do not substitute a smaller model on VRAM
grounds and reason from the metrics — Ministral is the named fallback and it
is *already measured as not good enough by ear*. Any substitute re-runs A3.

THE PROMPT IS THE ONE THAT PASSED, VERBATIM
--------------------------------------------
`_SYSTEM_PROMPT` and `_EXEMPLARS` are the `strict_few_shot` arm from
`eval/run_roman_arabic_probe.py`, copied rather than imported: `eval/` is not
a backend dependency, and a prompt that silently drifts from the one the gate
was run against would invalidate the gate without anything failing. Each
numbered rule names an OBSERVED failure from SS8/SS8b rather than describing
the task in general terms.

Prompting is not a lever here in either direction: Gemma's four arms scored
within noise of each other (30–33/45) because it already holds the
constraints, exactly inverting §10a where Qwen could not hold them at all.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

__all__ = ["GemmaTransliteratorBackend"]

#: The strict prompt, stated as numbered non-negotiables rather than prose.
_SYSTEM_PROMPT = """You convert Roman Urdu into Perso-Arabic Urdu script. \
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

#: The six exemplars of the `strict_few_shot` arm, chosen to demonstrate the
#: contract's HARD cases rather than more of the same:
#:   code-switch kept in Latin; pure Urdu with no Latin at all; a sentence
#:   STARTING with an English word (catches a model that picks the output
#:   script from token 1); SMS orthography with dropped vowels; chat
#:   abbreviations surviving verbatim; and the mixed decision — a person's
#:   name converts while an institution's name stays Latin, in one sentence.
_EXEMPLARS: tuple[tuple[str, str], ...] = (
    (
        "Hamein database ka backup lena hoga aur phir server dobara restart karna paray ga.",
        "ہمیں database کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔",
    ),
    (
        "Aray yaar chhoro na, koi baat nahi, agli dafa dekh lein ge.",
        "ارے یار چھوڑو نا، کوئی بات نہیں، اگلی دفعہ دیکھ لیں گے۔",
    ),
    (
        "Client ke saath meeting reschedule ho gayi hai, ab Friday ko hai.",
        "client کے ساتھ meeting reschedule ہو گئی ہے، اب Friday کو ہے۔",
    ),
    (
        "Mjhe smjh nhi aa rha k ye kese hua.",
        "مجھے سمجھ نہیں آ رہا کہ یہ کیسے ہوا۔",
    ),
    (
        "asap reply karna plz, boss ne bola hai k urgent hai",
        "asap reply کرنا plz، boss نے بولا ہے کہ urgent ہے",
    ),
    (
        "Dr. Saeed ne Aga Khan Hospital mein appointment de di hai.",
        "ڈاکٹر سعید نے Aga Khan Hospital میں appointment دے دی ہے۔",
    ),
)


def build_system_prompt(extra_instruction: str = "") -> str:
    """
    The gate-passing prompt, optionally with the user's own instruction
    appended.

    The addition goes LAST and is framed as a preference, so it cannot quietly
    displace rule 3 ("do not translate, explain, summarise") — that rule is
    what stands between this feature and a model that answers the text instead
    of converting it. The server-side validator in `domain/transliterate.py`
    is the actual enforcement; this is only about not inviting the failure.
    """
    block = "\n\n".join(f"Roman: {roman}\nUrdu: {urdu}" for roman, urdu in _EXEMPLARS)
    prompt = f"{_SYSTEM_PROMPT}\n\nExamples:\n{block}"
    if extra_instruction.strip():
        prompt += (
            "\n\nThe user has also asked for the following. Follow it only where it does "
            "not conflict with the rules above:\n"
            f"{extra_instruction.strip()}"
        )
    return prompt


#: A transliteration is about as long as its input, so the budget scales with
#: the request rather than being a flat ceiling. Generous: running out mid
#: sentence yields a truncated conversion that the validator's length check
#: then rejects, costing a full ~19 GB reload to retry.
def _token_budget(text: str) -> int:
    return max(256, int(len(text) * 1.5) + 128)


class GemmaTransliteratorBackend:
    """One Gemma process. Transliteration only — no `synth`, no audio."""

    runtime = "gemma_transliterator"

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._template_kwargs: dict[str, Any] = {}
        self.loaded_model_id: str | None = None

    def load(self, model_id: str, hf_repo: str, hf_revision: str) -> float:
        t0 = time.time()
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

        # Golden rule 7: resolve the PINNED snapshot on disk and load from
        # that path rather than trusting `main`.
        model_path = snapshot_download(repo_id=hf_repo, revision=hf_revision)
        self._tokenizer = AutoTokenizer.from_pretrained(model_path)

        cfg = AutoConfig.from_pretrained(model_path)
        load_kwargs: dict[str, Any] = {"device_map": "cuda"}

        if getattr(cfg, "quantization_config", None) is not None:
            # TRAP 1: a pre-quantized checkpoint (AWQ/GPTQ/bnb) carries its own
            # dtype. Forcing bfloat16 over it either errors or silently
            # DEQUANTIZES into VRAM this card does not have.
            #
            # TRAP 2: transformers pre-reserves an allocator block sized from
            # the UNQUANTIZED parameter count. For a 26B model whose 4-bit
            # weights are ~14 GiB it tried to reserve 22.36 GiB and OOMed on a
            # card with room for the actual weights. The warmup is purely an
            # allocator optimisation — neutralising it costs load speed and
            # changes no numerics, and it is applied ONLY on the quantized
            # path so unquantized loads are bit-for-bit unaffected.
            import transformers.modeling_utils as modeling_utils

            modeling_utils.caching_allocator_warmup = lambda *a, **k: None
        else:
            load_kwargs["dtype"] = torch.bfloat16

        try:
            self._model = AutoModelForCausalLM.from_pretrained(model_path, **load_kwargs)
        except ValueError as exc:
            # TRAP 3: several current instruct models declare a MULTIMODAL
            # config, so AutoModelForCausalLM refuses them outright even though
            # the text path is exactly what this uses.
            if "Unrecognized configuration class" not in str(exc):
                raise
            from transformers import AutoModelForImageTextToText

            self._model = AutoModelForImageTextToText.from_pretrained(
                model_path, **load_kwargs
            )

        # TRAP 4: a thinking model emits <think>…</think> BY DEFAULT, and left
        # on, every response is reasoning followed by the answer — which the
        # validator then judges as prose. The switch lives in the chat
        # template, so only pass it to templates that declare it.
        template = getattr(self._tokenizer, "chat_template", "") or ""
        self._template_kwargs = {"enable_thinking": False} if "enable_thinking" in template else {}

        self.loaded_model_id = model_id
        return time.time() - t0

    def transliterate(
        self, *, text: str, instruction: str = "", params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Convert one passage. Returns the raw string — validation is the API
        process's job (`domain/transliterate.py`), not this side of the wire.
        """
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("transliterate called before load")
        source = text.strip()
        if not source:
            return {"text": "", "gen_time_sec": 0.0}

        messages = [
            {"role": "system", "content": build_system_prompt(instruction)},
            {"role": "user", "content": f"Roman: {source}\nUrdu:"},
        ]
        # return_dict=True explicitly — without it, on this transformers
        # version the return shape is not interchangeable with what
        # `generate(**inputs)` expects and fails with an opaque AttributeError
        # on `.shape` deep inside generate(). Same trap as the analyzer.
        inputs = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt",
            return_dict=True, **self._template_kwargs,
        ).to(self._model.device)

        budget = int((params or {}).get("max_new_tokens") or _token_budget(source))
        t0 = time.time()
        out = self._model.generate(**inputs, max_new_tokens=budget, do_sample=False)
        gen_sec = time.time() - t0
        raw = self._tokenizer.decode(
            out[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )

        # A CLOSED <think> block means the switch was ignored but the answer is
        # still there, so take it. An UNCLOSED one means generation ran out of
        # budget mid-reasoning and there is no answer at all — leave it, and
        # let the validator reject it as the real failure it is.
        if "</think>" in raw:
            raw = raw.split("</think>", 1)[1]

        return {"text": raw.strip(), "gen_time_sec": gen_sec}

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._template_kwargs = {}
        self.loaded_model_id = None
        with contextlib.suppress(Exception):
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
