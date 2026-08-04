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

## voxcpm2 — runtime verified ✅

Repo **`openbmb/VoxCPM2`** @ `bffb3df5a29440629464e5e839f4d214c8714c3d`.
Class `voxcpm.core.VoxCPM`; model impl `voxcpm.model.voxcpm2.VoxCPM2Model`.

### Licensing gate: PASSED

Apache-2.0 on **both** weights and code (`VoxCPM/LICENSE`, `pyproject.toml`), and the model card
explicitly permits commercial use. Verified separately because weights and code are often licensed
differently.

### Measured on the A5000 (sm_86)

| Metric | Estimate was | **Measured** | Note |
|---|---|---|---|
| Peak VRAM | 8000 MB | **7300 MB** | nvidia-smi peak during generation; 6200 MB resident after load. Estimate was conservative. |
| RTF | 0.30 | **0.58** | English and Hindi alike, 3 samples. 1.9× the published figure — that was Ada; Ampere has no FP8. **Still passes the < 1.0 gate.** |
| Cold load | 45 s | **123.8 s** | True cold. 65.6 s once torch Inductor's compile cache is warm on disk. |

The load-time miss is the one that matters for UX: the UI would have promised "~45 s" and delivered
two minutes.

### Real API

```python
# voxcpm.core.VoxCPM — public generate() is (*args, **kwargs); the real
# signature lives on _generate:
_generate(text, prompt_wav_path=None, prompt_text=None, reference_wav_path=None,
          cfg_value=2.0, inference_timesteps=10, min_len=2, max_len=4096,
          normalize=False, denoise=False, retry_badcase=True,
          retry_badcase_max_times=3, retry_badcase_ratio_threshold=6.0,
          streaming=False, seed=None)
```

Zero-shot cloning needs `reference_wav_path` alone — **no reference transcript required**.
UI-worthy params: `cfg_value` (1.0–3.0, default 2.0), `inference_timesteps` (1–50, default 10).
The `retry_badcase*` and `min_len`/`max_len` knobs are safety internals and must NOT surface in the UI.

### ⚠️ Warm-up trap — must be handled in the runtime

The bundled warm-up (`optimize=True`) never passes a reference clip, so it does **not** compile the
cloning path. The first real cloning call then pays an extra **40–55 s** of `torch.compile`. A
runtime that uses the built-in warm-up will make every cold worker's first request look broken.

**The runtime must warm up with a real reference clip.**

### Other measured facts

- Sample rates: **16 kHz encoder input, 48 kHz output** (from `config.json`).
- Reference duration does not drive output length: 2 s / 10 s / 60 s all succeeded, no truncation,
  near-constant generation time.
- sm_86 clean — no flash-attn, no FP8, plain `scaled_dot_product_attention` + bf16.
- Deps: `torch 2.13.0+cu130`, `torchaudio 2.11.0`, `transformers 5.14.1`, `huggingface-hub 1.26.0`.

**This dependency set validates the architecture.** VoxCPM2 wants torch 2.13.0+cu130 while the pod's
base is 2.8.0+cu128 — they coexist only because each runtime gets its own venv and its own
interpreter in a separate process. A thread pool could not have done this, which is the first of the
three reasons subprocesses were chosen.

### NOT measured — gate still incomplete

- **CER** (needs Whisper-large-v3) and **speaker cosine** (needs a real embedding model). R3b used an
  MFCC-cosine + F0 proxy and reported English 0.9950 / Hindi 0.9926 similarity to the reference —
  suggestive that Hindi genuinely clones rather than falling back to a generic voice, but **flagged
  by R3b itself as a heuristic, not a verified embedding result.** R4b's harness does the real check.
- Denoiser (ZipEnhancer) VRAM and load time; concurrent-request VRAM; the 20-request soak.

**`LanguageSupport.verified` stays `False` for both cells** until CER and speaker cosine are measured.
RTF passes; two of three gates are still unrun.

## Urdu pipeline

⏳ R4 in flight. Decisive question: does `ai4bharat-transliteration` support `ur` in the
**indic → roman** direction? If not, Perso-Arabic → Devanagari collapses to NLLB translation, which
is a different and worse operation — translation changes words, transliteration only changes script.

R4 also delivers the eval harness that scores every cell in this document.
