"""
AI Voice Clone Studio — Gemma-4-31B script converter.

FOUR CONVERSIONS, AND THEY EXIST FOR TWO DIFFERENT REASONS
-----------------------------------------------------------
    Roman Urdu → Perso-Arabic   TO SPEAK IT. OmniVoice declares no `(ur, LATIN)`
                                cell, so this is what makes Roman Urdu
                                speakable at all. **Gated 2026-08-16.**
    Devanagari → Roman Urdu     TO READ IT. YouTube writes Urdu videos' captions
                                in Devanagari, and Roman is what the owner can
                                comfortably edit.
    Devanagari → Perso-Arabic   TO SPEAK IT, in one hop, when the caption is
                                good enough that nobody wants to edit it.
    Perso-Arabic → Roman Urdu   TO READ IT, for the rare video whose captions
                                arrive in Urdu script.

The last three are ungated.

WHICH ONE RUNS IS NOT THIS MODULE'S DECISION. The source is detected from the
text and the target is the caller's preference — "readable" and "speakable" are
different things to want, and only the person about to do something with the
text knows which. `domain/transliterate.py` owns both; this file owns the
prompt each pair needs.

The edit-then-speak route therefore costs TWO Gemma loads (Devanagari → Roman →
*edits* → Perso-Arabic), and R4b measured hop chains compounding errors. That
cost buys one thing: the correction step lands in the script the owner can
actually read. The one-hop route is there for when they would rather not pay
it.

Devanagari is a SOURCE FORMAT and never a target — nothing here converts *into*
it, and `routing.py` still refuses to render it. See
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

__all__ = [
    "GemmaTransliteratorBackend",
    "SOURCE_LATIN",
    "SOURCE_DEVANAGARI",
    "SOURCE_ARABIC",
    "TARGET_PERSO_ARABIC",
    "TARGET_ROMAN",
]

#: Scripts, as the plain strings `domain.language.Script` already uses for its
#: values. Plain strings, not that enum, because this module runs in a SEPARATE
#: VENV: importing from `domain/` would drag pydantic and the config stack into
#: every runtime environment to carry four constants.
SOURCE_LATIN = "latin"
SOURCE_DEVANAGARI = "devanagari"
SOURCE_ARABIC = "arabic"

TARGET_PERSO_ARABIC = "perso_arabic"
TARGET_ROMAN = "roman"

#: How each script is named in the prompt header and, separately, as the turn
#: prefix. The prefix matters as much as the header: the exemplars are formatted
#: with it, so a mismatch would show the model six `Roman:` turns and then ask
#: it a `Hindi:` question.
_SCRIPT_NAMES = {
    SOURCE_LATIN: ("Roman Urdu", "Roman"),
    SOURCE_DEVANAGARI: ("Hindi written in Devanagari script", "Hindi"),
    SOURCE_ARABIC: ("Urdu written in Perso-Arabic script", "Urdu"),
    TARGET_PERSO_ARABIC: ("Perso-Arabic Urdu script", "Urdu"),
    TARGET_ROMAN: ("Roman Urdu (Urdu written in Latin letters)", "Roman"),
}

#: Rules 3-5 are the same in every direction and are the reason the prompt is
#: shared rather than duplicated: they are about the WORDS, which do not change,
#: not about either script. Rules 1 and 2 name a target, so they vary.
#:
#: `{wrong_office}` is rule 2's counter-example, and it has to vary because a
#: literal one leaks a script. The Perso-Arabic "دفتر" sat in EVERY prompt,
#: including the ones whose target is Roman — a rule against translating,
#: illustrated in the script that direction must not produce. Caught by a test
#: asserting no prompt shows a script its conversion is not about, which is
#: exactly the class of error a prompt cannot report itself.
_SHARED_RULES = """\
2. English words stay EXACTLY as they are, in Latin letters, character for character. \
Do not translate them. Do not change their capitalisation. "office" stays "office", \
never "{wrong_office}". "GitHub" stays "GitHub", never "github".
3. Do not translate, explain, summarise, correct, or improve anything. The output must be the \
same sentence the user wrote, in a different script.
4. Keep the user's own wording, tone and word order, including informal or misspelled words. \
Do not add or remove punctuation the user did not write.
5. Names of people and places are Urdu words -- convert them. Brand, product and company names \
written in Latin are English -- leave them.

Output the converted sentence and nothing else. No preamble, no notes, no quotation marks."""

#: Rule 2's counter-example, per target: the wrong thing to do with "office".
#: Always written in the TARGET script, because the point of the example is
#: "you converted a word you should have left alone", and an example in some
#: third script demonstrates a different mistake than the one being forbidden.
_WRONG_OFFICE = {
    TARGET_PERSO_ARABIC: "دفتر",
    TARGET_ROMAN: "daftar",
}

#: Rule 1, per target. The Perso-Arabic wording is verbatim from the arm A3 run
#: 3 passed on; do not reword it to match the Roman one's style.
_RULE_ONE = {
    TARGET_PERSO_ARABIC: (
        "1. Write Urdu words in Perso-Arabic script. Every Urdu word must be converted -- "
        "never leave part of the sentence in the script it came in."
    ),
    # Two extra clauses the Perso-Arabic rule does not need. "Spell them the way
    # Urdu speakers text" is the whole point of this direction — the output is
    # read and edited by a person, so ALA-LC scholarly transliteration would be
    # a worse answer than everyday chat spelling even though it is more
    # principled. And Roman Urdu has no single correct spelling, which the model
    # must be told or it will hedge.
    TARGET_ROMAN: (
        "1. Write Urdu words in Latin letters, spelled the way Urdu speakers actually text -- "
        "\"mujhe\", \"kaise\", \"nahi\". Not a scholarly transliteration: no diacritics, no "
        "macrons, no special characters. Every Urdu word must be converted -- never leave part "
        "of the sentence in the script it came in."
    ),
}

#: The strict prompt, stated as numbered non-negotiables rather than prose.
_SYSTEM_PROMPT = """You convert {source} into {target}. \
You change the SCRIPT ONLY. You never change the words.

Rules, in order of importance:

{rule_one}
{shared_rules}"""

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

#: Devanagari, taken from the same six sentences.
#:
#: **NOTHING NEW WAS AUTHORED FOR EITHER DEVANAGARI SET.** The pairs below are
#: assembled from three columns of the same six sentences — Devanagari input
#: written here, Roman and Urdu outputs lifted verbatim from `_LATIN_EXEMPLARS`
#: above, which are the strings A3 run 3 passed on by ear.
#:
#: That is a safety property, not a shortcut. Authoring six new gold strings
#: would put unreviewed text into the PROMPT, where an error does not merely
#: score badly — it teaches the model the error. (The corpus already carries an
#: open task for native-speaker review of gold strings written that way.)
#: Writing a Devanagari *input* for a known-good output is the far weaker
#: claim, and the only one worth making without a native speaker.
#:
#: Five of the six hard cases carry over unchanged. The sixth does not: SMS
#: orthography with dropped vowels ("Mjhe smjh nhi") has no Devanagari form, so
#: that slot demonstrates the DANDA instead — `।` U+0964, which has no
#: counterpart in either target.
_DEVANAGARI_SOURCES: tuple[str, ...] = (
    "हमें database का backup लेना होगा और फिर server दोबारा restart करना पड़ेगा।",
    "अरे यार छोड़ो ना, कोई बात नहीं, अगली दफ़ा देख लेंगे।",
    "Client के साथ meeting reschedule हो गई है, अब Friday को है।",
    "मुझे समझ नहीं आ रहा कि ये कैसे हुआ।",
    "asap reply करना plz, boss ने बोला है कि urgent है",
    "डॉ. सईद ने Aga Khan Hospital में appointment दे दी है।",
)

#: Devanagari → Perso-Arabic. Wired, but **not the transcript path** — the
#: transcript path goes via Roman so the owner can edit it. Kept because it is
#: the one-hop route for anyone who does not want to edit, and because deleting
#: it would not simplify anything: both sets come from the same six sentences.
_DEVANAGARI_TO_URDU: tuple[tuple[str, str], ...] = tuple(
    zip(_DEVANAGARI_SOURCES, [urdu for _, urdu in _LATIN_EXEMPLARS], strict=True)
)

#: Devanagari → Roman Urdu. **This is the transcript hop.** Its outputs are the
#: INPUT side of `_LATIN_EXEMPLARS` — the corpus's own Roman Urdu, which is both
#: reviewed and exactly the spelling style rule 1 asks for. So this set
#: demonstrates the house style rather than asserting one.
#:
#: WHAT NEITHER DEVANAGARI SET DECIDES: an English loanword already spelled in
#: Devanagari (मीटिंग for "meeting"). Auto-generated Hindi captions are full of
#: them and rule 2 has nothing to say — there are no Latin letters to preserve.
#: Writing "meeting" and writing "miting" are both defensible and *nothing here
#: has measured which one a reader prefers or which one survives the second hop
#: better*. An exemplar would be inventing the answer, so there is none.
_DEVANAGARI_TO_ROMAN: tuple[tuple[str, str], ...] = tuple(
    zip(_DEVANAGARI_SOURCES, [roman for roman, _ in _LATIN_EXEMPLARS], strict=True)
)

#: Perso-Arabic → Roman Urdu, which is `_LATIN_EXEMPLARS` READ BACKWARDS. Not a
#: trick: the corpus pairs each Roman sentence with its gold Urdu, and which
#: column is the input is the only thing that differs between the two
#: directions. Nothing new is authored, and the two directions cannot drift
#: apart because they are the same six sentences.
#:
#: For the rare YouTube video whose captions come in Urdu script rather than
#: Devanagari — the owner has not found one, but the transcript panel would
#: otherwise offer nothing at all for it.
_URDU_TO_ROMAN: tuple[tuple[str, str], ...] = tuple(
    (urdu, roman) for roman, urdu in _LATIN_EXEMPLARS
)

#: Keyed on the PAIR, not on the source. The conversion IS the pair — Devanagari
#: means something different depending on where it is going, and a table keyed
#: on source alone could not express that.
_EXEMPLARS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    (SOURCE_LATIN, TARGET_PERSO_ARABIC): _LATIN_EXEMPLARS,
    (SOURCE_DEVANAGARI, TARGET_ROMAN): _DEVANAGARI_TO_ROMAN,
    (SOURCE_DEVANAGARI, TARGET_PERSO_ARABIC): _DEVANAGARI_TO_URDU,
    (SOURCE_ARABIC, TARGET_ROMAN): _URDU_TO_ROMAN,
}

#: The pair used when an unrecognised one is asked for. Roman Urdu →
#: Perso-Arabic is the only conversion here that has passed a listening gate,
#: so it is what an unexpected value degrades to.
_DEFAULT_PAIR = (SOURCE_LATIN, TARGET_PERSO_ARABIC)


def _resolve_pair(source_script: str, target_script: str) -> tuple[str, str]:
    """
    The (source, target) pair to actually run, falling back rather than raising.

    Falling back because the caller detected the source from the TEXT: an
    unexpected value means something like MIXED, and refusing outright would
    turn a merely-unusual input into a failed job. `(latin, perso_arabic)` is
    the gated conversion, so that is where it degrades to.

    `(latin, roman)` is deliberately absent: converting Roman Urdu to Roman
    Urdu is a no-op, and it falls back rather than being special-cased,
    because the validator would reject the identical output as an echo anyway.
    """
    pair = (source_script, target_script)
    return pair if pair in _EXEMPLARS else _DEFAULT_PAIR


def build_system_prompt(
    extra_instruction: str = "",
    source_script: str = SOURCE_LATIN,
    target_script: str = TARGET_PERSO_ARABIC,
) -> str:
    """
    The gate-passing prompt, optionally with the user's own instruction
    appended.

    The pair selects the header wording, both turn prefixes and the exemplar
    set together — they are one decision, not four, because a prompt that says
    "Hindi" over six `Roman:` examples is worse than either half alone.

    The extra instruction goes LAST and is framed as a preference, so it cannot
    quietly displace rule 3 ("do not translate, explain, summarise") — that
    rule is what stands between this feature and a model that answers the text
    instead of converting it. The server-side validator in
    `domain/transliterate.py` is the actual enforcement; this is only about not
    inviting the failure.
    """
    source, target = _resolve_pair(source_script, target_script)
    source_name, source_prefix = _SCRIPT_NAMES[source]
    target_name, target_prefix = _SCRIPT_NAMES[target]
    block = "\n\n".join(
        f"{source_prefix}: {src}\n{target_prefix}: {out}"
        for src, out in _EXEMPLARS[(source, target)]
    )
    prompt = (
        _SYSTEM_PROMPT.format(
            source=source_name,
            target=target_name,
            rule_one=_RULE_ONE[target],
            shared_rules=_SHARED_RULES.format(wrong_office=_WRONG_OFFICE[target]),
        )
        + f"\n\nExamples:\n{block}"
    )
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
        target_script: str = TARGET_PERSO_ARABIC,
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

        # The user turn's two prefixes must be the ones the exemplars were
        # formatted with, so they come from the same resolution rather than
        # literals — including the FALLBACK, which is why `_resolve_pair` runs
        # here too instead of the raw arguments being trusted.
        pair = _resolve_pair(source_script, target_script)
        _, source_prefix = _SCRIPT_NAMES[pair[0]]
        _, target_prefix = _SCRIPT_NAMES[pair[1]]
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(instruction, *pair),
            },
            {"role": "user", "content": f"{source_prefix}: {source}\n{target_prefix}:"},
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
