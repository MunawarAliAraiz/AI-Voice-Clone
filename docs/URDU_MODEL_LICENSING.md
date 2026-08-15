# Licensing report — Urdu/Hindi TTS candidates

**Date:** 2026-08-14 · **Method:** every claim below was read at a primary source (HF model card
metadata, the repo's actual `LICENSE` file, or the project page). Nothing here is inferred from a
blog post, an aggregator, or a repository being publicly downloadable.

> **Two questions, never conflated.**
> *Technically promising* — could this model do the job?
> *Commercially deployable* — may we ship it?
>
> This project currently permits **non-commercial weights for the owner's personal use behind the
> `VCS_API_KEY` gate**. That permission does **not** make those weights commercially licensed, and
> the two are tracked separately in every row below. A model marked 🔴 is usable for the bake-off and
> for personal use; it is **not** clearable for a product without a separate licence.

## The trap this report exists to catch

**A repository's `LICENSE` file governs its code, not necessarily its weights.** Confirmed twice here:

- **OmniVoice** — GitHub `LICENSE` is Apache-2.0 (Xiaomi Corp), but the HF model card states
  **CC-BY-NC for the pre-trained model**. Multiple 2026 blog posts call it "Apache 2.0, free for
  commercial use"; they read the code licence. The weights are non-commercial.
- **F5-TTS lineage** — SWivid's F5-TTS *code* is MIT while its *base checkpoints* are CC-BY-NC. Any
  checkpoint fine-tuned from those weights inherits the restriction regardless of the tag its
  uploader chose. This is why this project already removed every "permissively-tagged" English F5.

**Licence inheritance is real.** A fine-tune cannot be more permissive than its base. An XTTS-v2
fine-tune inherits Coqui CPML (non-commercial) even when the uploader tags it MIT.

---

## Classification key

| | Meaning |
|---|---|
| 🟢 | Safe/clear — permissive on code *and* weights, no blocking conditions |
| 🟡 | Usable with conditions — gating, attribution, or an unresolved provenance question |
| 🔴 | Non-commercial or research-only — personal use only here |
| ❓ | Ambiguous — insufficient primary evidence; treat as 🔴 until resolved |

---

## Summary

| Model | Code licence | **Weights licence** | Dataset | Attribution | Redistribution | Commercial | Class |
|---|---|---|---|---|---|---|---|
| **VoxCPM2** | Apache-2.0 | **Apache-2.0** | not stated | no | permitted | ✅ yes | 🟢 |
| **Chatterbox ML v3** | MIT | **MIT** | not stated | no | permitted | ✅ yes | 🟢 |
| **dots.tts** | Apache-2.0 | **Apache-2.0** | not stated | no | permitted | ✅ yes | 🟢 |
| **Indic Parler-TTS** | Apache-2.0 | **Apache-2.0** | IndicVoices-R etc. | no | permitted | ✅ yes | 🟢 |
| **IndicF5** | MIT | **MIT** (gated) | Rasa, IndicTTS, LIMMITS, IndicVoices-R | no | gate applies to derivatives | ⚠️ probably | 🟡 |
| **OmniVoice** | Apache-2.0 | **CC-BY-NC** | 581k h, open-source speech | yes (BY) | NC only | ❌ no | 🔴 |
| **Higgs Audio v3 TTS** | not stated | **Boson Research & Non-Commercial** | not stated | credit required (Creator Use Grant) | restricted | ⚠️ monetized *content* ok; productising the model needs a separate licence | 🔴 |
| `zohann/urdu-tts` | — | MIT tag, **empty README** | not stated | unknown | unknown | ❓ | ❓ |
| `XTTS-v2-Urdu-FT` | — | inherits **Coqui CPML** | not stated | — | restricted | ❌ no | 🔴 |
| **MegaTTS3** | — | — | — | — | **encoder not released** | ❌ | 🔴 |

---

## Per-model detail

### VoxCPM2 — 🟢 the current production model
- **Code:** Apache-2.0 · **Weights:** Apache-2.0. The card states "Apache-2.0 license, free for
  commercial use" covering both.
- **Generated audio:** no stated restriction. **Fine-tuned weights:** Apache-2.0 permits derivatives;
  the card explicitly advertises SFT and LoRA fine-tuning from 5–10 min of audio.
- **Relevant caveat, not a licensing one:** its published list is 30 languages including Hindi and
  Arabic but **not Urdu**.
- Source: <https://huggingface.co/openbmb/VoxCPM2>

### Chatterbox Multilingual v3 — 🟢 already integrated
- **Code and weights:** MIT. No Urdu in its language set. Embeds a **Perth watermark** in every
  output by default — not a licensing restriction, but it is a property of the audio you ship.
- Source: <https://huggingface.co/ResembleAI/chatterbox>

### dots.tts — 🟢
- **Code and weights:** Apache-2.0, stated as "Released under Apache-2.0" on the weights card.
- Card asks for consent-aware reference-audio policies and watermarking — **recommendations, not
  licence terms**. Urdu is not enumerated; the card concedes higher WER on "script-divergent and
  under-represented languages (Arabic, Hindi, Turkish, Vietnamese)".
- Source: <https://huggingface.co/rednote-hilab/dots.tts-soar>

### Indic Parler-TTS — 🟢 licence, but disqualified on capability
- **Weights:** Apache-2.0 (HF card metadata; India's AIKosh catalogue says MIT — trust the HF repo).
- **Officially supports Urdu** among 21 languages. But an AI4Bharat maintainer stated on the repo:
  *"This model is not intended for Voice Cloning and neither will it support in the future."* It is
  description/caption-conditioned. **Fails the core requirement**, on capability, not licence.
- Source: <https://huggingface.co/ai4bharat/indic-parler-tts/discussions/8>

### IndicF5 — 🟡 conditions plus one unresolved question
- **Weights:** MIT per card metadata, but the repo is **access-gated** — you must accept terms and
  supply an `HF_TOKEN`. Downstream users describe it as "MIT; gated — accept their terms before
  redistributing derivatives publicly", so the gate is a practical redistribution condition on top
  of MIT.
- **Voice cloning:** the card requires explicit permission for any cloned voice; unauthorized
  cloning is prohibited.
- **⚠️ Unresolved provenance.** The card thanks "the authors of F5-TTS for their invaluable
  contributions and inspiration" but never states whether the weights were **trained from scratch**
  or **initialised from SWivid's CC-BY-NC F5-TTS checkpoints**. Third parties describe it only as
  "built on the F5-TTS architecture". If it *was* initialised from those weights, an MIT release is
  hard to reconcile with the base licence. **Fine for the bake-off and personal use; get this in
  writing from AI4Bharat before any commercial reliance.**
- Source: <https://huggingface.co/ai4bharat/IndicF5>

### OmniVoice — 🔴 non-commercial weights
- **Code:** Apache-2.0 (`LICENSE`, Xiaomi Corp, no weights carve-out in the file itself).
- **Weights:** the HF card states **CC-BY-NC** for the pre-trained model. The card is authoritative
  for the weights; the repo `LICENSE` covers the code.
- **CC-BY-NC** implies attribution *and* a non-commercial limit, and it extends to derivatives —
  a LoRA trained on these weights inherits it.
- Prohibits unauthorized cloning, impersonation, fraud.
- **Why it is still in the bake-off:** Urdu `ur`/`urd` with **211.27 h** of training data, more than
  its Hindi (117.17 h), at 0.6B params.
- Sources: <https://huggingface.co/k2-fsa/OmniVoice> · <https://github.com/k2-fsa/OmniVoice>

### Higgs Audio v3 TTS — 🔴 explicitly research/non-commercial
- **Weights:** "Boson Higgs TTS 3 Research and Non-Commercial License". The card is blunt:
  *"Production, hosted APIs, embedding in a product/service, or reselling the model requires a
  separate commercial license."*
- **Creator Use Grant — broader than first recorded here.** Re-read at the source 2026-08-14: it is
  **free for digital creators, explicitly including monetized content** (podcasts, videos, social
  posts). The one condition is visible credit to Boson AI's Higgs Audio in the audio or accompanying
  text — "not hidden at the bottom of credits". So a creator publishing monetized *content* is
  covered; what still needs a separate commercial licence is productising the **model** — hosted
  APIs, embedding it in a product/service, or reselling it. An earlier revision of this file
  described the grant as narrow and non-lifting; that understated it.
- **Practical consequence for this project:** the Creator Use Grant does not help us, because a
  voice-cloning studio *is* "embedding the model in a service". It would matter if the owner only
  ever published generated clips.
- Prohibits non-consensual cloning, impersonation, fraud, election deception, biometric surveillance.
- **Why it is in the bake-off:** Urdu appears in the **WER/CER < 5 production-quality tier**, flagged
  🇵🇰🇮🇳 — the only candidate whose own documentation acknowledges Pakistani Urdu.
- **⚠️ Arm F could not be run** (2026-08-14). transformers 5.15.0 does not implement
  `higgs_multimodal_qwen3`, `config.json` has `auto_map: null` so `trust_remote_code` cannot supply
  it, the only documented self-hosting path is the `lmsysorg/sglang-omni:dev` Docker image (Docker
  is not installed on the pod and cannot nest in a RunPod container), and mainline pip `sglang` has
  no Higgs model. **No claim is made about its Urdu quality — we never heard it.**
- Sources: <https://huggingface.co/bosonai/higgs-audio-v3-tts-4b> · <https://www.boson.ai/blog/higgs-audio-v3-tts>

### `zohann/urdu-tts` — ❓ treat as 🔴
- Tagged MIT, but the **model card README is empty** — no base model, no language statement, no
  usage terms. The linked GitHub project describes a Tortoise-TTS derivative fine-tuned on
  South-Asian accents. **Licence inheritance from Tortoise is unverified**, and an MIT tag on a
  repo with no documentation is not evidence the weights are MIT-clean.
- Sources: <https://huggingface.co/zohann/urdu-tts> · <https://github.com/ahmedHanzala/urdu-tts>

### `suhaibrashid17/XTTS-v2-Urdu-FT` — 🔴 by inheritance
- An XTTS-v2 fine-tune. XTTS-v2 is **Coqui Public Model License (CPML)**, non-commercial, whatever
  tag a fine-tune carries. Independently, this repo's `CLAUDE.md` rule 6 bans reintroducing XTTS v2,
  and that ban stands on its own — it is not lifted by the personal-use decision.

### MegaTTS3 — 🔴 on distribution, not licence
- The **WaveVAE encoder is not released**: you submit a sample to ByteDance to receive `.npy` voice
  latents. Arbitrary local voice cloning is therefore not possible regardless of licence.
- Source: <https://github.com/bytedance/MegaTTS3>

---

## Datasets (relevant to any future fine-tune)

| Dataset | Scale | Pakistani? | Licence | Commercial |
|---|---|---|---|---|
| **UrduSpeech (2026)** | 156 h; 59.2 h standard + 89.4 h code-switched + 7.3 h Pakistani English | ✅ Pakistan + diaspora (PTV/YouTube archival) | **CC-BY-4.0** | ✅ with attribution 🟢 |
| **Common Voice Urdu** | ~81 h → 301 h by release | mixed | **CC0** | ✅ 🟢 |
| IndicVoices-R | 1,704 h / 22 languages, incl. Urdu | Indian Urdu | open; check per-language | 🟡 |
| Urdu-ONYX-WAV | TTS-prepared | unknown | modified Apache-2.0, **mandatory attribution** | 🟡 |
| Urdu Multi-Speaker TTS (Mozilla DC) | ~10 h, 3 speakers | unknown | **CC-BY-NC-4.0** | ❌ 🔴 |
| FutureBeeAI Urdu TTS | studio, per-artist | ✅ | commercial licence only | 💰 |

**Source-provenance caveat on UrduSpeech:** it is CC-BY-4.0 as *released*, but is built from YouTube
and PTV archival broadcast material. The dataset licence is the licence we rely on; be aware the
underlying recordings have their own rights holders if this ever moves to a commercial product.

---

## Conclusions

1. **No commercially-safe, open-source, Urdu-capable voice-cloning model was found.** The two
   candidates that genuinely list Urdu *and* clone from reference audio — Higgs Audio v3 and
   OmniVoice — are **both non-commercial**. Every permissively-licensed cloner either omits Urdu
   (VoxCPM2, Chatterbox, dots.tts) or omits cloning (Indic Parler-TTS).
2. **The only commercially-clean route identified is fine-tuning**: VoxCPM2 (Apache-2.0 on code and
   weights, advertises LoRA from 5–10 min) on UrduSpeech (CC-BY-4.0, Pakistani, 57% code-switched).
   This is a **route to investigate, not a validated solution** — nothing has been trained or heard,
   and no claim is made here about the quality it would produce.
3. **IndicF5's provenance question should be resolved in writing** before any commercial reliance,
   even though it is fine for the bake-off and personal use.
4. **Weights licences must keep being read separately from code licences.** Two of the ten candidates
   here differ between the two, and in both cases the permissive-looking answer was the wrong one.
