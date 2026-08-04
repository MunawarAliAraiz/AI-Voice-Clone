# Technical Report: Permissively-Licensed Urdu Voice Cloning

**Date:** 2026-08-04
**Question:** Can we build "Roman Urdu text → speech that sounds like *me* speaking Pakistani Urdu,"
using only commercially-permissive (Apache/MIT/BSD) zero-shot models?
**Short answer:** Not with any model tested, and the reason is **not** the one we spent the day chasing.

---

## 1. What was tested

One reference throughout: the owner's own voice, 6.67 s of Urdu (`eval/fixtures/voice_urdu.wav`).
One target sentence, everyday register. Human verdict is authoritative; the harness (Whisper CER +
ECAPA-TDNN cosine) is a **screen, not a verdict** — established when VoxCPM2 passed CER and nearly
passed cosine yet sounded like a stranger.

| # | Model | License | Pipeline | CER | Speaker cosine | **Human verdict** |
|---|---|---|---|---|---|---|
| 1 | F5 OpenBible-Urdu | CC-BY-SA | Perso-Arabic (native) | 0.96 | 0.04 | noise — "aaah aaah" |
| 2 | F5 OpenBible-Urdu | CC-BY-SA | Devanagari | 0.47 | 0.14–0.19 | words audible, not me |
| 3 | **VoxCPM2** | Apache-2.0 | Roman→Devanagari | **0.07** | **0.686** | **best; Urdu-ish, not my voice** |
| 4 | Chatterbox (standard) | MIT | Roman→Devanagari | — | — | Urdu-ish, not my voice |
| 5 | Chatterbox (max-ref) | MIT | Roman→Devanagari | — | — | not my voice |

Owner's ranking: **VoxCPM2 > Chatterbox-standard > Chatterbox-max-ref.** All "sound like Urdu/Hindi
but not close to my voice."

---

## 2. The core finding — separate the two failures

Two independent things were failing and we initially conflated them:

**(A) Intelligibility / does-it-sound-Urdu** — *solved.* CER went 0.96 → 0.07 purely by fixing the
input script (Perso-Arabic → Devanagari, because every usable checkpoint's tokenizer is Devanagari).
VoxCPM2 produces clean, Urdu-sounding speech.

**(B) Speaker identity / does-it-sound-like-me** — *not solved by anything.* Cosine never exceeded
0.69, and human verdict is uniformly "not my voice" across **three different architectures** (F5,
VoxCPM2, Chatterbox) and two configs.

The decisive point: **(B) does not depend on (A).** Speaker identity is carried by the reference
*audio* through the model's speaker encoder — **not** by the text. Transliteration rewrites the text;
it never touches the speaker-embedding path. VoxCPM2 proves this directly: near-perfect
intelligibility (CER 0.07) coexisted with a generic voice. Better text did not, and could not, buy a
better voice.

**So transliteration is not the voice bottleneck.** (Answer to Q2 below.)

---

## 3. Answers to the four questions

### Q1 — Any permissively-licensed model that zero-shot clones Urdu?

**No, on the evidence.** Three permissive architectures were tried on the real target voice; all
produce intelligible Urdu in a **generic** voice, not the owner's. The one *native* Urdu checkpoint
(F5 OpenBible-Urdu) is additionally Devanagari-tokenized and Bible-domain, and clones worst of all.

Root cause is almost certainly **out-of-distribution speaker encoding**: these encoders are trained
overwhelmingly on English + high-resource languages. A ~7 s clip of a Pakistani Urdu male voice is
outside that distribution, so the extracted embedding is weak and the decoder falls back to its
"average" voice. This is a property of the *speaker encoder*, not of Urdu text.

### Q2 — Is transliteration fundamentally limiting voice quality?

**No.** It limits *pronunciation fidelity* (some words garble; the model applies Hindi phonotactics),
and it is why output leans "Hindi-accented." But the **voice-identity** failure is independent and
would persist even with a perfect native-script pipeline, because identity comes from the audio
encoder, not the text. Fixing transliteration would make it sound *more Urdu*; it would not make it
sound like *you*.

### Q3 — Best architecture for production?

Ranked by likelihood of actually delivering "sounds like me":

1. **Speaker-adaptive fine-tuning (LoRA / few-shot) on the target voice.** Breaks the "zero-shot"
   constraint but is the only approach with a strong prior of success — it teaches the model the
   voice instead of hoping a 7 s clip captures it. ~2–10 min of the owner's audio; minutes of
   training. VoxCPM2 (Apache-2.0) is the natural base.
2. **A model with a South-Asian-trained speaker encoder**, if a permissive one exists (not yet
   found). Would keep zero-shot.
3. **Longer / cleaner reference** (30–60 s vs 7 s) as a cheap zero-shot lever — likely improves
   cosine somewhat, unlikely to clear the "sounds like me" bar alone given the OOD-encoder diagnosis.

### Q4 — VoxCPM2, Chatterbox, or train our own?

- **Production pipeline base: VoxCPM2.** Best intelligibility (CER 0.07), best human ranking,
  Apache-2.0 on weights *and* code, 7.3 GB, RTF ~0.58. Keep the Roman-Urdu → Devanagari front-end
  (`ai4bharat-transliteration`, MIT, verified one-hop).
- **The voice problem needs fine-tuning, not a different zero-shot model.** Chatterbox and VoxCPM2
  fail the same way; there is no evidence a third zero-shot model would differ.
- **Do not keep tuning zero-shot knobs.** `cfg_weight`/`exaggeration` moved nothing meaningful — the
  ceiling is the encoder, not the sampler.

---

## 4. Recommendation

1. **Ship the intelligibility pipeline now** (Roman Urdu → Devanagari → VoxCPM2). It works and is
   permissive. Set expectations: "a natural Urdu voice," **not** "your voice," until step 2.
2. **Prototype LoRA fine-tuning of VoxCPM2** on 2–10 min of the owner's Urdu. This is the single
   experiment most likely to change the outcome, and per the standing rule it has a clear mechanistic
   reason to (it trains the identity the encoder can't infer). Everything else is noise around a fixed
   ceiling.
3. **Product/licensing decision for the owner:** accept "generic Urdu voice" for a true zero-shot
   MVP, or invest in per-speaker fine-tuning for real cloning. If neither fits, the permissive
   constraint itself may need revisiting — no free zero-shot model clones this voice today.

---

## 5. Closed — do not re-investigate

- F5 OpenBible-Urdu: Devanagari vocab (0 Arabic chars); EMA and nuqta-folding both ruled out; not
  viable even with correct input.
- Zero-shot speaker knobs (`cfg_weight`, `exaggeration`) on Chatterbox: no meaningful effect.
- Transliteration as the *voice* bottleneck: ruled out — identity is audio-encoder-bound, text-independent.
- Chatterbox API: `from_pretrained(device)` only (no `t3_model` arg); `generate(text, language_id,
  audio_prompt_path, exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p)`;
  `resemble-perth` ships broken and must be stubbed; no `ur` in its language set.
