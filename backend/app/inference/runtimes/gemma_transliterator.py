"""
AI Voice Clone Studio — Gemma-4-31B → Perso-Arabic transliterator.

Source scripts: **Latin (Roman Urdu)** and **Devanagari (Hindi captions)**.
Target is always Perso-Arabic, because that is the only Urdu script any model
here renders. Devanagari is a SOURCE FORMAT and never a target language — see
`docs/TRANSCRIPT_IMPORT.md`.

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

__all__ = ["GemmaTransliteratorBackend", "SOURCE_LATIN", "SOURCE_DEVANAGARI"]

#: Source scripts, as the plain strings `domain.language.Script` already uses
#: for its values. Plain strings, not that enum, because this module runs in a
#: SEPARATE VENV: importing from `domain/` would drag pydantic and the config
#: stack into every runtime environment to carry two constants.
SOURCE_LATIN = "latin"
SOURCE_DEVANAGARI = "devanagari"

#: What the source script is called in the prompt and in the turn prefix. The
#: prefix matters as much as the header: the exemplars below are formatted with
#: it, so a mismatch would show the model six `Roman:` turns and then ask it a
#: `Hindi:` question.
_SOURCE_NAMES = {
    SOURCE_LATIN: ("Roman Urdu", "Roman"),
    SOURCE_DEVANAGARI: ("Hindi written in Devanagari script", "Hindi"),
}

#: The strict prompt, stated as numbered non-negotiables rather than prose.
#: `{source}` is the only thing that varies by source script — every rule below
#: is about the TARGET and the words, which do not change.
_SYSTEM_PROMPT = """You convert {source} into Perso-Arabic Urdu script. \
You change the SCRIPT ONLY. You never change the words.

Rules, in order of importance:

1. Write Urdu words in Perso-Arabic script. Every Urdu word must be converted -- \
never leave part of the sentence in the script it came in.
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
_LATIN_EXEMPLARS: tuple[tuple[str, str], ...] = (
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

#: The Devanagari set, derived from the Latin one rather than written fresh —
#: **the Urdu side of all six is byte-identical to the pairs above.**
#:
#: That is deliberate, and it is a safety property, not a shortcut. Those Urdu
#: strings are the ones A3 run 3 actually passed on by ear. Authoring six NEW
#: gold Urdu strings would put unreviewed Urdu into the prompt itself, where an
#: error does not merely score badly — it teaches the model the error. (The
#: corpus already carries an open task for native-speaker review of gold
#: strings written this way.) Writing a Devanagari *input* for a known-good
#: Urdu *output* is the far weaker claim, and the only one worth making
#: without a native speaker.
#:
#: Five of the six hard cases carry over unchanged. The sixth does not: SMS
#: orthography with dropped vowels ("Mjhe smjh nhi") has no Devanagari
#: equivalent, so that slot instead demonstrates the DANDA — `।` U+0964, which
#: has no counterpart in the target and must become `۔`. Its Urdu output is
#: still the same string.
#:
#: WHAT THIS SET DELIBERATELY DOES NOT DECIDE: what to do with an English
#: loanword already spelled in Devanagari (मीटिंग for "meeting"). Auto-generated
#: Hindi captions are full of them, and the Latin contract's rule 2 has nothing
#: to say about it — there are no Latin letters to preserve. Converting it to
#: میٹنگ and leaving it as मीटिंग are both defensible, and *nothing here has
#: measured which one OmniVoice says better*. Adding an exemplar would be
#: inventing that answer, so there is none: the listening gate decides it, and
#: until then the model is unguided on exactly one case, which is an honest
#: gap rather than a guess.
_DEVANAGARI_EXEMPLARS: tuple[tuple[str, str], ...] = (
    (
        "हमें database का backup लेना होगा और फिर server दोबारा restart करना पड़ेगा।",
        "ہمیں database کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔",
    ),
    (
        "अरे यार छोड़ो ना, कोई बात नहीं, अगली दफ़ा देख लेंगे।",
        "ارے یار چھوڑو نا، کوئی بات نہیں، اگلی دفعہ دیکھ لیں گے۔",
    ),
    (
        "Client के साथ meeting reschedule हो गई है, अब Friday को है।",
        "client کے ساتھ meeting reschedule ہو گئی ہے، اب Friday کو ہے۔",
    ),
    (
        "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।",
        "مجھے سمجھ نہیں آ رہا کہ یہ کیسے ہوا۔",
    ),
    (
        "asap reply करना plz, boss ने बोला है कि urgent है",
        "asap reply کرنا plz، boss نے بولا ہے کہ urgent ہے",
    ),
    (
        "डॉ. सईद ने Aga Khan Hospital में appointment दे दी है।",
        "ڈاکٹر سعید نے Aga Khan Hospital میں appointment دے دی ہے۔",
    ),
)

_EXEMPLARS = {
    SOURCE_LATIN: _LATIN_EXEMPLARS,
    SOURCE_DEVANAGARI: _DEVANAGARI_EXEMPLARS,
}


def build_system_prompt(
    extra_instruction: str = "", source_script: str = SOURCE_LATIN
) -> str:
    """
    The gate-passing prompt, optionally with the user's own instruction
    appended.

    `source_script` selects the header wording, the turn prefix and the
    exemplar set together — they are one decision, not three, because a prompt
    that says "Hindi" over six `Roman:` examples is worse than either.

    An unknown script falls back to Latin rather than raising. The caller has
    already detected the script from the text, so an unexpected value means
    something like MIXED — and Roman Urdu is the source this was gated on.

    The extra instruction goes LAST and is framed as a preference, so it cannot
    quietly displace rule 3 ("do not translate, explain, summarise") — that
    rule is what stands between this feature and a model that answers the text
    instead of converting it. The server-side validator in
    `domain/transliterate.py` is the actual enforcement; this is only about not
    inviting the failure.
    """
    source_name, prefix = _SOURCE_NAMES.get(source_script, _SOURCE_NAMES[SOURCE_LATIN])
    exemplars = _EXEMPLARS.get(source_script, _LATIN_EXEMPLARS)
    block = "\n\n".join(f"{prefix}: {src}\nUrdu: {urdu}" for src, urdu in exemplars)
    prompt = f"{_SYSTEM_PROMPT.format(source=source_name)}\n\nExamples:\n{block}"
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
        self,
        *,
        text: str,
        instruction: str = "",
        source_script: str = SOURCE_LATIN,
        params: dict[str, Any] | None = None,
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

        # The user turn's prefix must be the same one the exemplars were
        # formatted with, so it comes from the same table rather than a literal.
        _, prefix = _SOURCE_NAMES.get(source_script, _SOURCE_NAMES[SOURCE_LATIN])
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(instruction, source_script),
            },
            {"role": "user", "content": f"{prefix}: {source}\nUrdu:"},
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
