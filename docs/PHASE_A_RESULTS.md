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

## f5_openbible_urdu — pipeline runs, OUTPUT QUALITY NOT VALIDATED ⚠️

> **A native Urdu speaker listened to `out_urdu_explicit_reftext.wav` and could not understand it.**
>
> The test used an **English reference clip with an English transcript** to generate Urdu:
>
> ```
> REF_AUDIO = f5_tts/infer/examples/basic/basic_ref_en.wav
> REF_TEXT  = "Some call me nature, others call me mother nature."
> GEN_TEXT  = <Urdu, Perso-Arabic>
> ```
>
> F5 is in-context: it conditions on `(ref_audio, ref_text)` as a prefix and continues into
> `gen_text`. Two compounding faults here — the acoustic prior is English, and the English reference
> text is tokenized through this checkpoint's **Urdu-specific `vocab.txt`**. Unintelligible output is
> the expected result of that setup, so **this does not yet condemn the checkpoint.**
>
> **"Non-silent, RMS 0.092" was never evidence of intelligibility.** It was recorded as if it were.
> RMS proves a waveform exists; nothing more. The CER gate exists for exactly this and was not run
> on this file.
>
> **Measured, Whisper large-v3-turbo:**
>
> ```
> TARGET: آج موسم بہت خوشگوار ہے اور سورج آسمان پر چمک رہا ہے، لوگ باہر سیر کر رہے ہیں۔
> HEARD : اب آآآآآآآآآآآآآآآآآآآآآآآآآآآ…   (one vowel, repeating for 13s)
> CER   : 0.993   gate 0.25   FAIL
> ```
>
> Not "slightly wrong Urdu" — **degenerate repeat-loop output**, which is what a flow-matching model
> produces when its conditioning prefix is incoherent. This corroborates the mismatched-reference
> diagnosis rather than a broken checkpoint.
>
> **Re-test required** with Urdu reference audio + a matching Urdu transcript, then score with
> `eval_harness.py`. Until that passes, `LanguageSupport.verified` stays `False` and this cell must
> not be advertised.
>
> **Blocked on:** a ~10 s Urdu voice clip with its transcript. This is also the real product
> scenario — a user's own voice — so it is the right test, not a workaround.

Performance numbers below are still valid — they measure load, VRAM, and throughput, none of which
depend on output being intelligible.

**Generated:** 13.06 s @ 24 kHz, RMS 0.092/0.083 across two runs — non-silent, but see above.

| Metric | Estimate was | **Measured** |
|---|---|---|
| Peak VRAM | 4000 MB | **6112 MB** (`torch.cuda.max_memory_allocated`); 5845 MiB concurrent nvidia-smi |
| Cold load | 40 s | **7.0 s** — 5× pessimistic; by far the fastest spec |
| RTF | — | **0.23 cold / 0.20 warm** — comfortably inside the gate |

Loads as a raw `model_last.pt` training checkpoint (`model_state_dict`, `ema_model_state_dict`, …)
into the stock `f5-tts` `DiT` class. No custom code, no `trust_remote_code`. Also needs `vocab.txt`
and a Hydra arch YAML; vocoder is `vocos` (`charactr/vocos-mel-24khz`).

```python
model_cfg = OmegaConf.load(CONFIG_YAML)
model_cls = get_class(f"f5_tts.model.{model_cfg.model.backbone}")   # f5_tts.model.DiT
vocoder   = load_vocoder(vocoder_name="vocos", is_local=False, device="cuda")
model     = load_model(model_cls, model_cfg.model.arch, CKPT_PT,
                       mel_spec_type="vocos", vocab_file=VOCAB_TXT,
                       use_ema=True, device="cuda")
ref_a, ref_t = preprocess_ref_audio_text(ref_audio_path, ref_text)
wav, sr, _ = infer_process(ref_a, ref_t, gen_text, model, vocoder,
                           mel_spec_type="vocos", device="cuda")
```

### Confirmed behaviours

- **Reference limit ~12 s, silent truncation.** Verified with a real 23.8 s clip: log said
  `"Audio is over 12s, clipping short."`, output stayed valid. No exception reaches the caller.
- **Hidden Whisper measured.** Blank `ref_text` loads `openai/whisper-large-v3-turbo`: **39.5 s
  cold** (includes downloading weights), **5.1 s warm**. Always pass `ref_text`.
- Deps: `f5-tts 1.1.22`, `torch 2.8.0+cu128`, `transformers 5.14.1`, `vocos 0.1.0`, `numpy 2.4.6`.
  No transitive breakage.

### ⚠️ Trap that changes the scheduler

**Post-hoc VRAM readings under-report the peak by ~5×.** Sampling nvidia-smi 20 s after inference
showed ~1092 MiB; concurrent sampling at 200 ms during the same run caught **5845 MiB**. Allocator
caches are released between requests.

So admission control must size from the spec's recorded `vram_mb` (measured under load), **not** from
a live free-VRAM reading taken between requests. A live reading detects *external* processes; it does
not predict peak. Recorded in `scheduler.py::_free_vram_mb`.

### Also learned

`curl -C -` resume produced a file matching the target byte count exactly that was still internally
corrupt (`PytorchStreamReader failed reading file data/277`). Re-downloaded cleanly via
`huggingface_hub.hf_hub_download`. **Never trust byte count alone after a resumed download.**

---

## f5_indic — BLOCKED 🔒

`ai4bharat/IndicF5` is gated (`"gated": "auto"`). Every file except `README.md` returns
`GatedRepoError: 401`. Nothing beyond metadata has been verified.

Confirmed from the HF API config and model card: `"architectures": ["INF5Model"]`,
`"auto_map": {"AutoModel": "model.INF5Model"}`, `"model_type": "inf5"`, tagged `custom_code`, and the
card's own snippet uses `AutoModel.from_pretrained(..., trust_remote_code=True)`.

**Therefore one F5 runtime class is NOT enough.** Wave 3 needs two loader paths: a generic raw-F5
checkpoint loader (OpenBible-Urdu shaped) and a dedicated `AutoModel(trust_remote_code=True)` path.
And because IndicF5 executes repo code, its pinned revision is a genuine security control.

**Unblock:** accept the license at huggingface.co/ai4bharat/IndicF5 and set `HF_TOKEN` on the pod.

## f5_openf5_en — DROPPED ❌

No permissive English F5 checkpoint exists. ~100 HF repos searched; every English-capable one derives
from `SWivid/F5-TTS` (CC-BY-NC-4.0). The permissive-tagged candidates are license-washing:
`lucasnewman/f5-tts-mlx` (MIT) says in its own README the weights are reshaped from SWivid;
`H5N1AIDS/F5-TTS-ONNX`, `kevinwang676/F5-TTS`, `ABUS-AI/F5-TTS-v0.1` have empty READMEs and no
provenance; `zeeshiii05/E2-F5-TTS` tags a math-reasoning dataset and is not credible.

English routes to `chatterbox_ml_v3` (MIT).

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

## Urdu pipeline — ANSWERED ✅

`ai4bharat-transliteration` **1.1.3, MIT**. Import path is `ai4bharat.transliteration` (the dist name
does not match the package). Both directions support `ur`, confirmed by execution:

| Route | Result |
|---|---|
| Roman Urdu → Perso-Arabic | works — `shukriya` → `شکریہ`, `mohabbat` → `محبت` |
| Perso-Arabic → Roman | works — `آپ کیسے ہیں` → `aap kisay hain` |
| **Roman Urdu → Devanagari** | **direct one hop.** No Perso-Arabic intermediate. |
| Perso-Arabic → Devanagari | **no direct hop exists** — only two-hop via Roman |

### The unexpected result: Devanagari output beats Perso-Arabic output

Feeding identical Roman strings to the same engine for `lang_code="hi"` vs `"ur"`, the **Hindi output
was consistently more accurate**. `"hai"` → `है` correct every time, while the Urdu hop produced `ہی`
— a different real word — every time.

So routing Roman Urdu through Devanagari is not a compromise forced by licensing. On this evidence
it is the *better* path, presumably because the multilingual model saw far more Hindi romanization in
training than Urdu.

### Perso-Arabic → Devanagari compounds errors — keep it opt-in and lossy

Two-hop is the only route, and it degrades measurably:

```
مجھے آپ سے محبت ہے
  two-hop:  → "majhay aap say mohabat hay" → मझे आप से मोहबत है
  one-hop from clean Roman:                → मुझे आप से मोहब्बत है   (correct)
```

Gemination dropped, vowels wrong. **Never chain Perso-Arabic → Roman → Devanagari as a substitute
for Roman → Devanagari.** This validates `UrduStrategy.TRANSLITERATE` being explicit opt-in with
`lossy=True`.

### Vowel loss is genuine information loss, not fuzziness

Undotted Perso-Arabic, top-4 beams:

```
شکر  → ['shikar', 'shakar', 'shukar', 'shekar']
```

Both genuine readings — *shukr* (thanks) and *shakar* (sugar) — are valid for that spelling, and the
model's top-1 `shikar` (hunt) is **neither**. The model resolves purely from corpus frequency, with
no access to intended meaning. That is the honest basis for the `lossy` flag on this route.

---

## Eval harness ✅

`/workspace/engines-lab/r4-urdu/eval_harness.py` — built, working, and validated.

- **CER**: Whisper large-v3 (MIT), with script-aware normalization — NFC, Arabic-Indic and
  Devanagari digit folding, `۔ ؟ ، । ॥` punctuation stripping.
- **Speaker similarity**: SpeechBrain ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`, Apache-2.0)
  cosine.
- **RTF**: caller-supplied timing.
- Corpus at `corpus/`: 3 everyday-register Urdu Perso-Arabic sentences (deliberately *not*
  liturgical), hand-written Roman Urdu equivalents, 3 Hindi Devanagari, 3 English, plus
  `reference_speaker_10s.wav` (12.8 s, from VoxCPM's Apache-2.0 example assets — documented
  provenance, not a real person's voice without consent).

Sanity check: `cosine(ref, ref) = 1.0000` exactly.

Noted deviation: installing the eval stack bumped `.venv-eval`'s torch from 2.8.0+cu128 to
2.13.0+cu130. CUDA verified still working. Not the "silent downgrade to broken cu124" trap, but it
diverges from the documented baseline.

---

## ⚠️ First real gate results — and they contradict the proxy

Scored genuine VoxCPM2 output (cloned from the same reference, known target text):

| | CER (<0.25) | Speaker sim (>0.70) | RTF (<1.0) |
|---|---|---|---|
| English | 0.000 ✅ | 0.739 ✅ | 12.68 ❌ *(cold-start artifact; R3b measured 0.58 warm)* |
| **Hindi** | 0.086 ✅ | **0.678 ❌** | 0.58 ✅ |

**VoxCPM2 Hindi fails speaker similarity at 0.678 against a 0.70 gate.**

R3b's MFCC+F0 proxy had reported 0.9926 for Hindi and concluded cloning worked — and R3b correctly
labelled that a heuristic rather than a verified result. The real ECAPA embedding says otherwise.
**This is exactly why the gate requires a speaker-embedding model, and why proxy metrics must never
be promoted to verified.**

The English RTF failure is a measurement artifact — that run included the 40–55 s warm-up trap. Warm
RTF is 0.58.

### Not yet a delete decision

The plan says a failing cell is removed from the catalog. Before applying that to VoxCPM2 + Hindi:

- **n = 1.** One sentence, one reference clip.
- **0.678 vs 0.70 is borderline** — within plausible variance for a single sample.
- The reference clip is English-language speech; the model card warns that a language-mismatched
  reference degrades output and suggests `cfg_weight=0.0` to mitigate. That was not applied.

**Action for Wave 3:** re-run Hindi with 3 sentences and a language-matched reference before deciding.
If it still misses, Hindi routes to Chatterbox or IndicF5 and this cell is deleted — not shipped with
a caveat.

---

## 🔴 Urdu re-test with a NATIVE reference — still FAILS

Run with a correct setup: the user's own 6.67 s Urdu recording as reference, the matching Urdu-script
transcript as `ref_text`, and an everyday-register corpus sentence as `gen_text`. Reference was used
in full (6.61 s after preprocessing — no truncation).

```
target      : اگر تمہیں فارغ وقت ملے تو مجھے فون کر لینا، ہم کہیں باہر کھانا کھانے چلتے ہیں...
whisper hears: 'موسیقی'      ← the word for "music". Whisper does not hear speech at all.

CER         0.9636   (< 0.25)   FAIL
speaker sim 0.0434   (> 0.70)   FAIL
RTF         0.3331   (< 1.0)    PASS
OVERALL     FAIL
```

**This is the decisive result.** The previous failure was explicable by a mismatched English
reference. This one is not — the setup was correct. A speaker similarity of **0.043 is
indistinguishable from zero**: the output has no relationship to the reference voice. Combined with
Whisper transcribing 8 seconds of audio as the single word "music", the output is very likely not
intelligible speech at all.

Fast, cheap, and wrong: RTF 0.333 and 5776 MB. Performance was never the problem.

### What this does and does not establish

Established: `f5_openbible_urdu` as currently loaded does not clone Urdu. It cannot ship in this
state, and `LanguageSupport.verified` stays `False`.

NOT yet established: whether the checkpoint itself is unusable, or whether the loading is wrong. One
cheap hypothesis remains untested — `load_model(..., use_ema=True)`. `model_last.pt` is a *training*
checkpoint carrying both `model_state_dict` and `ema_model_state_dict`; if the EMA weights are
unpopulated or stale, garbage output is exactly what results. Re-running with `use_ema=False` is a
one-line experiment that would distinguish "bad checkpoint" from "wrong loading".

### Consequence if it does not recover

Native Perso-Arabic Urdu has no other permissively-licensed option — this was the only free native
Urdu cloning checkpoint in existence. Urdu would then ship exclusively via the transliteration route
(Perso-Arabic → Devanagari → a Hindi model), which R4b verified works and which the architecture
already supports as a first-class path with `lossy=True` and a visible route chip.

That is a product decision, not a technical one, and it belongs to the user.

Artifact: `/workspace/engines-lab/r1-f5/out_urdu_nativeref.wav`
