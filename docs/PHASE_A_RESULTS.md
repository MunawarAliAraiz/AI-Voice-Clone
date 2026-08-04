# Phase A results

Empirical validation of every (model × language × script) cell before it may be routed to.

**Gate:** CER < 0.25 (Whisper-large-v3) · speaker cosine > 0.70 · RTF < 1.0.
A cell that fails is **deleted from `catalog.py`**, not shipped with a caveat. Advertising a language
a model cannot actually speak is how the predecessor came to offer Urdu on XTTS.

Until a cell is recorded here as verified, `LanguageSupport.verified` stays `False` and
`ModelSpec.supports()` returns `False`, so routing will not select it. **The catalog currently
resolves nothing.** That is correct for this stage.

> **Status: IN PROGRESS.** Wave 1 research is running. Sections fill in as agents report.
> Every claim below must trace to a command that was actually executed. Anything read off a model
> card belongs under "Unverified claims", not in the results tables.

---

## Summary

| Spec | Repo | License | Runtime-verified? | Gate |
|---|---|---|---|---|
| `f5_openbible_urdu` | `multilingual-tts/F5-TTS-OpenBible-Urdu` | CC-BY-SA-4.0 | ⏳ R1 | — |
| `f5_indic` | `ai4bharat/IndicF5` | MIT | ⏳ R1 | — |
| `f5_openf5_en` | *unresolved* | Apache-2.0? | ⏳ R1 | — |
| `chatterbox_ml_v3` | `ResembleAI/chatterbox` | **MIT ✅** | ⏳ R2b | — |
| `voxcpm2` | *unresolved* | Apache-2.0? | ⏳ R3 | — |

---

## chatterbox_ml_v3

### Verified

| Fact | Value | How |
|---|---|---|
| HF repo | `ResembleAI/chatterbox` | HF API, files listed |
| HF commit (main) | `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18` | `X-Repo-Commit` header |
| GitHub commit | `5de7a54aa4e5e2baadb0182dde554908b48b85c2` | GitHub API |
| License — model card | MIT | HF model card |
| License — code | MIT | GitHub `resemble-ai/chatterbox` |
| License — package | MIT | PyPI `chatterbox-tts` 0.1.7 |

**Licensing gate: PASSED.** MIT confirmed independently across weights, code, and package — worth
noting because weights and code are frequently licensed differently, and this project cannot ship
non-commercial weights.

### Unverified claims (from the model card — NOT yet runtime-tested)

These are recorded so R2b can confirm or refute them. Do not implement against this section.

- 23 languages including `hi` (Hindi). A Hindi-finetuned variant `ResembleAI/Chatterbox-Multilingual-hi`
  also exists.
- API: `from chatterbox.mtl_tts import ChatterboxMultilingualTTS`,
  `.from_pretrained(device=..., t3_model="v3")`, `.generate(text, language_id=..., audio_prompt_path=...)`
- Params: `exaggeration` (0–1, default 0.5), `cfg_weight` (0–1, default 0.5)
- Reference text not required; reference audio alone is enough.
- Model card warns: if the reference clip's language does not match `language_id`, the output
  inherits the reference language's accent — mitigate with `cfg_weight=0.0`. **Directly relevant**,
  since Roman-Urdu-via-Devanagari will pair a Hindi `language_id` with an Urdu-speaker reference.

### Open dependency questions for R2b

`chatterbox-tts` 0.1.7 reportedly pins `numpy<2.0.0` and `torch==2.6.0`, while the pod has numpy
2.1.2 and torch 2.8.0+cu128. R2 marked this "compatible but newer" without testing it. Either it
resolves in a clean venv or it does not — R2b must settle it, because this is exactly the class of
unsatisfiable pin that broke the predecessor (`transformers>=4.57.6` vs fish-speech's `<=4.57.3`).

### Not measured

Peak VRAM · cold load time · RTF · reference-audio bounds · whether Hindi output actually clones the
reference speaker rather than falling back to a generic voice.

---

## f5_openbible_urdu / f5_indic / f5_openf5_en

⏳ R1 in flight. The decisive question: whether `ai4bharat/IndicF5` loads as a raw checkpoint into
the standard F5 class or requires `AutoModel(trust_remote_code=True)`. If the latter, the pinned
revision becomes a security control rather than hygiene.

## voxcpm2

⏳ R3 in flight. Must re-measure VRAM and RTF on **sm_86**; the published ~8 GB / RTF 0.30 figures
are Ada-generation. At ~8 GB this is the largest spec and constrains what can co-reside.

## Urdu pipeline

⏳ R4 in flight. Decisive question: does `ai4bharat-transliteration` support `ur` in the
**indic → roman** direction? If not, Perso-Arabic → Devanagari collapses to NLLB translation, which
is a different and worse operation — translation changes words, transliteration only changes script.

R4 also delivers the eval harness that scores every cell in this document.
