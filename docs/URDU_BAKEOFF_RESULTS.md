# Urdu bake-off — results

**Status: blind listening complete for arms A–E (130/130 clips, one rater — the owner). No model
has been chosen.** Arms H/I/J (IndicF5) remain blocked on an `HF_TOKEN`, and arm I is the plan's
central question — a real decision on the transliteration route cannot be made without it. §5 is
filled for A–E with the actual listening data; the licence-clean route (VoxCPM2, arms A–D) can be
compared now, but the full decision waits on IndicF5. Nothing here may set `LanguageSupport.verified`
— that still requires the owner's sign-off on an integration, not just a listening score.

**Date:** 2026-08-14 · **Pod:** RTX A5000 24 GB (sm_86), torch 2.8.0+cu128 · **Branch:** `feature/urdu-bakeoff`

| Tag | Meaning |
|---|---|
| `[CARD]` | verified at a primary source (model card, licence file, repo) |
| `[INFER]` | reasoning from `[CARD]` facts — **not established** |
| `[BENCH]` | measured by `eval/score_urdu_bakeoff.py` — a **screen**, not a ranking |
| `[LISTEN]` | human judgement — **none recorded yet** |

---

## 1. What was actually run

13-item corpus (`eval/fixtures/urdu_corpus.json`), **identical text in every arm**, two reference
speakers, 130 clips.

| Arm | Model | Representation | Owner (male) | Female | Question it answers |
|---|---|---|---|---|---|
| A | VoxCPM2 | Roman Urdu | ✅ 13 | ✅ 13 | today's production baseline |
| B | VoxCPM2 | Perso-Arabic | ✅ 13 | ✅ 13 | does it turn Arabic? |
| C | VoxCPM2 | Devanagari | ✅ 13 | ✅ 13 | Hindustani-equivalence route |
| D | VoxCPM2 + LoRA (run 2) | Perso-Arabic | ✅ 13 | ✅ 13 | does the LoRA help Urdu? |
| E | OmniVoice | Perso-Arabic | ✅ 13 | ✅ 13 | 211 h native Urdu, 0.6B |
| F | Higgs Audio v3 | Perso-Arabic | ⛔ could_not_run | ⛔ could_not_run | 🇵🇰-flagged production-tier Urdu |
| G | *(reporting slice)* | code-switched items | ✅ | ✅ | the `GitHub`/`office` cases — §4 |
| H | IndicF5 | direct Urdu Unicode | ⏸ blocked | ⏸ blocked | can its tokenizer even accept it? |
| I | IndicF5 | Urdu → Devanagari | ⏸ blocked | ⏸ blocked | **the central question** |
| J | IndicF5 | nuqta-preserving Devanagari | ⏸ blocked | ⏸ blocked | does preserving nuqta preserve Urdu phonemes? |

Arm G is not a separate synthesis run by design — it is the 3 code-switched corpus items sliced out
of every arm, so it compares like with like instead of introducing a fourth variable.

### Why arm F could not run `[CARD]`

Four independent checks, all failing:

1. **transformers 5.15.0** registers only `higgs_audio_v2` / `higgs_audio_v2_tokenizer`.
   v3 declares `model_type: higgs_multimodal_qwen3`,
   `architectures: ["HiggsMultimodalQwen3ForConditionalGeneration"]` — not importable.
2. The repo's `config.json` has **`auto_map: null`**, so `trust_remote_code=True` cannot supply the
   missing class. There is no remote code to trust.
3. Boson documents **one** self-hosting path: SGLang-Omni via the `lmsysorg/sglang-omni:dev` Docker
   image. **Docker is not installed on the pod**, and a RunPod container cannot nest one.
4. Mainline pip `sglang` has **no Higgs support at all** — no `higgs*.py` under
   `python/sglang/srt/models/`, and a GitHub code search for `HiggsMultimodalQwen3` across
   `sgl-project/sglang` returns **0 hits**. The `-omni:dev` image is a development fork.

Boson additionally lists **≥40 GB VRAM as known-good and 24 GB as "reported to work, not officially
verified"**; this pod is 24 GB, so even a successful source build would run outside verified
territory.

> **No claim is made about Higgs v3's Urdu quality. We never heard it.** It remains the only
> candidate whose own documentation acknowledges Pakistani Urdu, and that claim is still untested.

### Why arms H/I/J are blocked `[CARD]`

`ai4bharat/IndicF5` is `gated=auto`. Metadata reads fine without credentials; the 1.4 GB
`model.safetensors` returns **`GatedRepoError: 401 … Access to model ai4bharat/IndicF5 is
restricted`**. Because the *whole repo* is gated, `trust_remote_code` cannot fetch the shipped
`f5_tts/` modules either — nothing about this arm can be verified without a token.

**`gated=auto` means approval is automatic**: accept the terms on the model page, generate a read
token, and access is granted immediately. This is one click plus a token, not an application.

Also required: **a transcript of the female reference clip**. IndicF5 needs `ref_text`, and
`_load_indicf5` deliberately raises rather than accept a blank one — some F5 loaders silently run
Whisper on the reference instead of erroring, which would change what is being measured without
saying so.

---

## 2. Screening metrics `[BENCH]` — read the caveats first

**These numbers do not rank the arms.** They detect gross breakage — silence, wrong language,
garbage. Two reasons they cannot do more:

- **CER**: scored against the canonical Perso-Arabic for *every* arm, with Whisper always asked for
  Urdu (the only way arms stay comparable). Whisper's Urdu is weak and its script choice for
  Urdu/Hindi audio is unstable.
- **Speaker cosine**: ECAPA-TDNN is **English-trained and out-of-distribution** for these voices.
  This is not hypothetical — VoxCPM2 once passed CER and nearly passed cosine and still sounded like
  a stranger to the owner.

Medians over 13 items (a mean would let one garbled clip swing the result).

| Arm | Model | Repr. | Ref | CER ↓ | cos ↑ | RTF | Peak VRAM | Load |
|---|---|---|---|---|---|---|---|---|
| A | VoxCPM2 | roman | owner | 0.0625 | 0.6997 | 0.764 | 6423 MB | 81 s |
| A | VoxCPM2 | roman | female | 0.0625 | 0.7975 | 0.874 | 7051 MB | 131 s |
| B | VoxCPM2 | perso-arabic | owner | **0.1887** | 0.6664 | 0.763 | 6433 MB | 77 s |
| B | VoxCPM2 | perso-arabic | female | 0.0385 | 0.7489 | 0.751 | 7061 MB | 72 s |
| C | VoxCPM2 | devanagari | owner | **0.0187** | 0.6881 | 0.849 | 6439 MB | 69 s |
| C | VoxCPM2 | devanagari | female | 0.0213 | 0.7828 | 0.847 | 7061 MB | 68 s |
| D | VoxCPM2+LoRA | perso-arabic | owner | 0.0385 | 0.6621 | 0.943 | 6509 MB | 123 s |
| D | VoxCPM2+LoRA | perso-arabic | female | 0.0500 | **0.6889** | 0.910 | 7131 MB | 67 s |
| E | OmniVoice | perso-arabic | owner | 0.1489 | 0.7366 | **0.442** | **4495 MB** | 81 s |
| E | OmniVoice | perso-arabic | female | 0.0851 | 0.7938 | 0.736 | **4699 MB** | 85 s |

**Every runnable arm cleared gross breakage.** Nothing produced silence or the wrong language. That
is the entire safe conclusion from this table.

### Three observations, none of them conclusions

**a. The "VoxCPM2 reads Perso-Arabic as Arabic" hypothesis is _not_ supported symmetrically.**
Arm B's owner cell is the worst CER in the table (0.1887), which fits the hypothesis — but arm B's
*female* cell is 0.0385, among the best. A model applying Arabic phonology to Arabic script would do
so regardless of which voice it is cloning. So the single high number is more likely reference- or
ASR-specific than evidence of language misdetection. **The hypothesis stands unresolved and only
listening can settle it** `[INFER]`.

**b. Devanagari (arm C) has the lowest CER at both references.** Tempting, and *not* to be read as
"Devanagari is best". It is equally consistent with Devanagari producing Hindi-accented speech that
Whisper transcribes more confidently — exactly the outcome the owner already rejected by ear in
`docs/URDU_CLONING_REPORT.md`. **A CER win here is as likely to be a warning as a result.**

**c. Every female cell scores a higher cosine than its owner counterpart** (0.689–0.798 vs
0.662–0.737), across all five arms. A uniform offset across unrelated models points at ECAPA or the
reference recordings, not at model quality. Do not read it as "these models clone women better."

### Reference clips

| id | file | speaker | notes |
|---|---|---|---|
| `owner` | `eval/fixtures/voice_urdu.wav` | repo owner, male | pre-existing |
| `female` | `eval/fixtures/voice_urdu_female.wav` | female, consented | converted from the owner-supplied `.ogg`; **transcript still needed for arms H/I/J** |

`voice_urdu_female.wav` is **deliberately not committed** — it is a real person's voice and the
standing rule is that no voice data is committed without explicit sign-off. It lives on the pod and
on local disk only.

---

## 3. Blind listening `[LISTEN]` — arms A–E, 130/130 clips, one rater

**Caveats first, because n is small and this is a single listener.** 13 sentences × 2 references per
arm (25–26 scored cells per arm; two cells are missing a full score — see §3c). Medians, 1–5. This is
one person's judgement, not inter-rater-reliability-tested. Read differences of ~0.5–1.0 as signal;
read anything closer as noise.

### 3a. By arm, both references combined

| Arm | Model | Pron. | Natural | Identity | Prosody | Code-switch | n |
|---|---|---|---|---|---|---|---|
| A | VoxCPM2 (roman) | 3.0 | 3.0 | 4.0 | 4.0 | 4.0 | 26 |
| B | VoxCPM2 (perso-arabic) | 3.0 | 3.0 | 4.0 | 4.0 | 5.0 | 26 |
| C | VoxCPM2 (devanagari) | **4.0** | **4.0** | 4.0 | 4.0 | 5.0 | 25 |
| D | VoxCPM2 + LoRA | **4.0** | **4.0** | 4.0 | 4.0 | 5.0 | 25 |
| E | OmniVoice | **5.0** | 4.0 | 4.0 | 4.0 | **3.0** | 26 |

Three things this actually shows:

1. **Devanagari (C) and the LoRA (D) both beat Roman/Perso-Arabic VoxCPM2 on pronunciation and
   naturalness** (4.0 vs 3.0), while matching them on code-switch (5.0) and identity (4.0). Between
   arms C and D specifically, `[BENCH]`'s cosine numbers (§2) had flagged D as the *weakest* VoxCPM2
   arm on speaker identity — that did **not** show up in the blind identity score, which ties C and D
   at 4.0. This is exactly the case the two-axis separation exists to catch: an automated metric
   pointing one way and a human ear finding no difference.
2. **OmniVoice (E) is the best-rated pronunciation in the set (5.0) and the worst code-switch (3.0).**
   It is also the only NC-licensed model among the five that ran. If E were commercially clean this
   would be the strongest single result in the table; because it isn't, it is evidence about the
   *ceiling*, not a deployable answer.
3. **Roman Urdu (A) is not the pronunciation floor** — it ties B, both at 3.0, both below C/D/E. The
   `[INFER]` hypothesis from §1 (Roman→Hindi phonotactics, Perso-Arabic→possible Arabic phonology) is
   not confirmed by this: if that hypothesis were the whole story, A and B should differ more than
   they do, and neither should trail Devanagari (C) by a full point.

### 3b. By arm and reference speaker — does it generalize across voices?

| Arm | Owner (male) pron/nat/id/pros/cs | Female pron/nat/id/pros/cs |
|---|---|---|
| A | 3.0 / 3.0 / 4.0 / 3.0 / 3.0 | 3.0 / 4.0 / 4.0 / 4.0 / 5.0 |
| B | 4.0 / 3.0 / 4.0 / 3.0 / 5.0 | 3.0 / 3.0 / 4.0 / 4.0 / 5.0 |
| C | 4.0 / 4.0 / 4.0 / 4.0 / 5.0 | 4.0 / 3.5 / 4.0 / 4.0 / 5.0 |
| D | 3.5 / 4.0 / 4.0 / 4.0 / 4.5 | 4.0 / 4.0 / 4.0 / 4.0 / 5.0 |
| E | 5.0 / 4.0 / 4.0 / 4.0 / 3.0 | 5.0 / 4.0 / 4.0 / 4.0 / 3.0 |

**No arm collapses on either reference** — nothing here shows a model that works on one speaker and
fails on the other, which was the specific failure mode this two-reference design was built to catch
(plan correction #7). E is identical across both. C, D are close. A and B show the largest owner/
female split, concentrated in naturalness and code-switch — plausibly the same source-recording
factors noted in §2 (the reference clips' own quality), not a model asymmetry, but that is
`[INFER]`, unconfirmed.

### 3c. Free-text comments — one real finding, one page bug

**The corpus reads digits as digits, not as spoken Urdu number-words, and the owner flagged it
independently on four different clips across three arms** (A, C, D) and all three number/date items
(`date`, `num_ascii`, `num_eastern`):
> *"counting should also be in urdu like in urdu we say 'chouda' instead of fourteen"*
> *"45 is called 'pentalees' in urdu and 3 should be 'teen'"*

This is a **corpus authoring gap, not a model quality signal** — `eval/fixtures/urdu_corpus.json`'s
number items use numeral characters (`۳`, `3`) rather than spelled-out Urdu number words, so every
model is reading digits in whatever convention it defaults to, and none of them is being asked to
produce the actually-idiomatic form. This recurred across unrelated arms (A/C/D), which is exactly
the signature of a shared input problem rather than a per-model one. **Not fixed here** — flagged for
whoever next revises the corpus; fixing it would change the `date`/`num_ascii`/`num_eastern` items
for every arm, which is a change big enough to warrant its own re-run rather than a mid-analysis edit.

**Sharper still, per the owner's direct listen (2026-08-15, not independently re-verified by ear
here): within the `date` item's own sentence** — `یہ رپورٹ 14 اگست 2026 تک جمع کرانی ہے۔` ("this
report is due 14 August 2026") — **the day-of-month number (14) and the year (2026) were NOT
mispronounced equally.** 14 came out wrong (something close to "chodan" rather than چودہ/"chauda"),
while 2026 was rendered correctly and idiomatically as "do hazar chabees." That is consistent with
the corpus-authoring-gap explanation above rather than a separate defect: year numbers said aloud in
South Asian speech are conventionally read as a compound ("two thousand twenty-six" /
"دو ہزار چھبیس"), which is closer to how these models plausibly learned digit sequences from training
data, whereas a bare 1-2 digit cardinal like "14" has no such strong convention pulling it toward the
correct spoken form. Same root cause, different surface symptom — not yet independently reproduced
against a specific arm's audio, flagged here for whoever next revisits this corpus.

**Two clips (of 130) could not be scored: `[C/female/num_eastern]` and `[D/owner/owner_02_file]`,
both reported as "audio is muted, I am unable to unmute it."** Checked directly: both source WAVs
have normal peak amplitude (0.99 and 0.92) and are not silent, so this reads as a page/browser
playback glitch on those two specific embedded clips, not a synthesis defect. **1.5% of the corpus
has an unresolved listening gap** — the underlying audio exists and can be re-served if a clean
re-listen on just these two is wanted; recorded rather than guessed at.

### 3d. The two axes, filled

| A. Urdu quality | B. Voice cloning |
|---|---|
| Pakistani pronunciation — §3a: C/D/E > A/B | speaker identity — flat at 4.0 across every arm; `[BENCH]` cosine differences did not survive to the ear |
| Urdu vocabulary — not separately scored; folded into pronunciation | consistency across sentences — no per-sentence collapse observed |
| phonology — same | male-reference performance — §3b, no arm collapses |
| naturalness, prosody — C/D lead naturalness; prosody is flat (4.0) everywhere | female-reference performance — §3b, no arm collapses |
| code-switching — E is the outlier (3.0 vs 4.5–5.0 elsewhere); see §4 for why the automated metric could not have predicted this | |

Identity sits at a flat 4.0 for every arm at both references — the blind listen did **not** find a
speaker-identity problem in the five arms that ran. That is a materially different picture from the
automated cosine numbers in §2, which spread from 0.66 to 0.79. **Trust the 4.0, not the spread** —
this is precisely why the plan required listening in the first place.

---

## 4. Arm G — code-switching `[BENCH]`

The 3 corpus items mixing English into Urdu (`office`, `GitHub` / `pull request`, technical terms),
sliced from every arm.

| Arm | Ref | CER (code-switch) | CER (all 13) | cos (code-switch) |
|---|---|---|---|---|
| A | owner | 0.3867 | 0.0625 | 0.6573 |
| A | female | 0.3733 | 0.0625 | 0.7950 |
| B | owner | 0.3733 | 0.1887 | 0.6518 |
| B | female | 0.2075 | 0.0385 | 0.7489 |
| C | owner | 0.3733 | 0.0187 | 0.6223 |
| C | female | 0.4478 | 0.0213 | 0.7588 |
| D | owner | 0.3600 | 0.0385 | 0.6462 |
| D | female | 0.3733 | 0.0500 | 0.6665 |
| E | owner | 0.3733 | 0.1489 | 0.7368 |
| E | female | 0.3600 | 0.0851 | 0.7775 |

⚠️ **This is largely a measurement artifact and must not be reported as a model failure.** CER is
scored against Perso-Arabic with Whisper asked for Urdu. When the audio contains "GitHub" or
"office", Whisper-ur has to render Latin-script English words into Urdu script, and the canonical
reference keeps them as Latin islands. The edit distance that produces is structural — it would
appear even for a perfect rendition. The uniformity of the numbers (8 of 10 cells in 0.36–0.39,
across four different models) is itself the tell: this is measuring the metric, not the models.

**Code-switching can only be judged by ear.** It is one of the five listening criteria.

---

## 5. Decision table — filled for A–E, still open for H/I/J

**Not a final decision.** Arm I is the plan's central question and has not run. What follows is
accurate for the five arms that could be compared; it is not the whole bake-off.

| Candidate | Pron. | Natural | Identity | Code-switch | Speed | VRAM | Commercial | Verdict |
|---|---|---|---|---|---|---|---|---|
| VoxCPM2 (roman / A) | 3.0 | 3.0 | 4.0 | 4.0 | 0.76 RTF | 6.4 GB | 🟢 | today's baseline; beaten by C/D on the two axes that moved |
| VoxCPM2 (perso-arabic / B) | 3.0 | 3.0 | 4.0 | 5.0 | 0.76 RTF | 6.4 GB | 🟢 | ties A on pron/natural; no advantage found for native script over Roman |
| VoxCPM2 (devanagari / C) | 4.0 | 4.0 | 4.0 | 5.0 | 0.85 RTF | 6.4 GB | 🟢 | **best commercially-clean arm on pronunciation + naturalness** |
| VoxCPM2 + LoRA (D) | 4.0 | 4.0 | 4.0 | 5.0 | 0.94 RTF | 6.5 GB | 🟢 | ties C exactly; LoRA's `[BENCH]` cosine regression did not survive to the ear |
| OmniVoice (E) | 5.0 | 4.0 | 4.0 | 3.0 | 0.44 RTF | 4.5 GB | 🔴 NC | **highest ceiling observed, but not commercially usable**; weak code-switch |
| Higgs v3 (F) | — | — | — | — | — | — | 🔴 NC | **could not run** — see §1 |
| IndicF5 (H/I/J) | — | — | — | — | — | — | 🟡 gated | **blocked on token** — arm I unanswered |

### The 9 questions

| # | Question | Answer |
|---|---|---|
| 1 | Best model for Pakistani Urdu, by listening? | **OmniVoice (E), 5.0 pronunciation** — but non-commercial. Among arms that ran, no single model is unambiguously best across all five criteria (E trades pronunciation for code-switch). Incomplete: IndicF5 never ran |
| 2 | Best commercially safe option? | **VoxCPM2 with Devanagari input (arm C), or the LoRA (arm D) — statistically tied.** Both beat Roman/Perso-Arabic VoxCPM2 (A/B) by a full point on pronunciation and naturalness while matching them on identity and code-switch |
| 3 | Can IndicF5 produce Pakistani Urdu via Devanagari/phonetic conversion? | **still blocked** — arms I/J never ran. What §3 *does* show is that Devanagari input measurably helped VoxCPM2 (arm C beats A/B) — one data point in favor of the transliteration route being worth testing on IndicF5 too, not proof it will transfer |
| 4 | Does native Urdu work better or worse than transliteration? | **Neither — arms A (Roman) and B (Perso-Arabic) tied each other**, and both trailed Devanagari (C). Native Perso-Arabic input showed no advantage over Roman in this data |
| 5 | Does VoxCPM2 + LoRA meaningfully improve Urdu? | **Improves over the raw Perso-Arabic baseline (B) to match Devanagari (C)** on pronunciation/naturalness (3.0→4.0), and does **not** show the identity regression `[BENCH]`'s cosine numbers suggested — identity stayed flat at 4.0. The LoRA is not obviously better than *just switching B's input to Devanagari*, though, so the gain may be from matching C's representation rather than the fine-tune itself; not disentangled here |
| 6 | Which model should we integrate? | **Owner's call, not made here.** The commercially-clean leaders are C and D. IndicF5 could still beat both if arm I ever runs. No integration should start before that's resolved or explicitly deferred |
| 7 | Should we add an Urdu transformation layer? | **Weak yes, so far.** Devanagari transliteration (arm C) is the only text transform that moved the needle on VoxCPM2 in this data. `normalize_urdu` (character-only normalization) remains untested — it's a no-op on all 13 corpus items, so ladder rung B is unverified regardless of arm C's result |
| 8 | What stays the English backend? | **VoxCPM2** — unchanged and not under test here |
| 9 | Best future fine-tuning strategy? | Unchanged from §1: VoxCPM2 (Apache-2.0) on UrduSpeech (CC-BY-4.0) remains a route to investigate, not validated. The LoRA result in Q5 is a small positive data point for that direction generally, but this LoRA was trained on different data than UrduSpeech, so it does not directly answer this question |

Question 8 is unconditionally answered. Questions 2, 4, 5, 7 have real answers now, all scoped to
"among the arms that ran." Questions 1, 3, 6 stay open until arm I either runs or is explicitly
deferred by the owner.

### 5a. Arm D shipped, then withdrawn on owner listening (2026-08-15)

Arm D was integrated as `voxcpm2_urdu_lora` on 2026-08-14 (§6 below, item 3's "owner decides" — the
owner picked D). It was tested end-to-end the following day through the real running app, on the
pod's real GPU, with a real enrolled voice — not just the bake-off's isolated clips. **The owner's
verdict on that real-use test was that base VoxCPM2 sounded better than the LoRA.**

This directly contradicts D's blind-listen median in §3a (4.0/5 across the board, tied with C). Per
this project's own repeatedly-stated rule — owner listening is authoritative over an automated
metric — the same principle extends here: a *later, more direct* listen (the actual voice, the actual
app, not an isolated 4-second clip in a randomized listening page) overrides an earlier one. The spec
was removed from the catalog the same day.

**What this most likely means, in light of Q5's own caveat above:** Q5 already flagged that D's gain
over B "may be from matching C's representation rather than the fine-tune itself; not disentangled."
The withdrawal is consistent with that — the fine-tune itself may not have been doing much, and 36
clips / 300 steps was always a small POC (`docs/VOXCPM_LORA_POC.md`). This is *not* evidence that
LoRA fine-tuning can't work for Urdu, only that this particular small personal one didn't hold up.

**What replaced it:** `voxcpm2_urdu_arabic` — arm B's config (base VoxCPM2, Perso-Arabic input, no
fine-tune), still `experimental_listing=True`, still `verified=False`. Mediocre (3.0/5 pronunciation)
but the working, honest baseline for Perso-Arabic Urdu until a better arm (OmniVoice, IndicF5, or a
properly-scaled fine-tune) is integrated and verified. The runtime's LoRA-loading plumbing
(`lora_local_path`/`lora_hf_repo` on `ModelSpec`, `VoxCPMBackend.load()`'s kwargs) was kept — it is
generic, tested, and costs nothing unused — so any future LoRA, on a larger dataset, ships without
re-threading the wire protocol.

### 5b. Arm Eprod — real owner listen `[LISTEN]` (2026-08-15)

The remaining step §5's arm-Eprod note flagged — an owner listen against the actual production-backend
clips, not just their CER/cosine numbers — is done. All 26 clips (13 items × owner + female reference,
`eval/results/urdu_bakeoff/arm_Eprod_{owner,female}/`) were listened to directly.

**Verdict: mostly very good, with one systematic, real weakness — numbers and dates.** Consistent
across both references:

| Item | Verdict | Detail |
|---|---|---|
| `owner_01_sick` | ✅ perfect | — |
| `owner_04_late` | ✅ correct | code-switched "office"/"late" both landed |
| `long_multiclause` | ✅ correct | "meeting" pronounced correctly here specifically |
| `owner_03_deadline`, `owner_05_github`, `names`, `colloquial` | ✅ good | no issue flagged |
| `num_ascii` | 🔴 wrong | digits (3, 45) mispronounced, **and "meeting" itself came out wrong in this item** despite being correct in `long_multiclause` — a digit nearby seems to drag down an otherwise-fine word, not just the digit itself |
| `num_eastern` | 🔴 wrong | same failure, Eastern Arabic-Indic digit glyphs (۳، ۴۵) — the digit *script* isn't the variable, the fact that they're digits is |
| `date` | 🔴 wrong | both 14 and 2026 mispronounced — **unlike the VoxCPM2/LoRA-era finding in §3c**, where 2026 alone came out correct ("do hazar chabees") and only 14 failed. OmniVoice fails on both; this is a per-model difference, not a universal digit rule |
| `abbreviations` | 🔴 partial | "برائے کرم" (please) mispronounced; **"URL" rendered as "oo r l"** rather than the expected English-letter reading |
| `technical` | 🔴 partial | "database" pronounced with an Arabic-accented T |
| `owner_02_file` | 🟡 minor | "check" comes out closer to "chaeck" — small vowel drift, not a real problem |

**One concrete case of the numeric gate being wrong in the *optimistic* direction, not just the
pessimistic one already on record:** `owner_05_github` has the single worst CER in the whole arm-Eprod
run (0.4478, both references) — the corpus's most heavily code-switched sentence
(`GitHub`/`pull request`/`create`/`review`). It was listened to and judged **good**. The likely
explanation is the same one §2 already gives for CER in general: Whisper's own transcription of
code-switched audio is unreliable, so a high CER here is at least partly an ASR artifact, not proof the
speech is bad. This is the mirror image of the numbers finding below — CER *underclaimed* quality on
`owner_05_github` and *overclaimed* it on nothing (the numbers items' CER was already visibly elevated
in the score files — 0.13/0.13/0.32 for owner, 0.09/0.15/0.24 for female — so the metric did flag them,
just not with enough magnitude relative to `owner_05_github`'s much higher number to make severity
rank correctly by CER alone).

**Net finding:** the failure is narrow and specific — number/date reading and a handful of
technical-English loanwords (URL, database) — not a general Urdu-quality problem. Ordinary
conversational Urdu, including code-switched English words in casual use ("office", "late"), came
through clean on both voices. This mirrors a pattern already on record for a different model+context in
§3c (digit pronunciation being a weak point generally), reinforcing that this is closer to "Urdu TTS
digit-reading is an unsolved, cross-model gap" than "OmniVoice specifically is bad at this."

### 5c. Numbers/dates fix, verified by ear (2026-08-15)

`eval/urdu_numerals.py` expands digit runs (ASCII and Eastern Arabic-Indic) into the Urdu cardinal
words they're actually spoken as before synthesis — `14` → `چودہ`, `2026` → `دو ہزار چھبیس` — the same
technique every production TTS frontend uses for numbers. Re-synthesized the 3 broken items
(`num_ascii`, `num_eastern`, `date`) with expanded text through the real production
`OmniVoiceBackend`, both references (`eval/run_number_fix_check.py`,
`eval/results/urdu_bakeoff/number_fix_check/`, before/after comparison page at
`eval/results/urdu_bakeoff/number_fix_check.html`).

**A real bug surfaced during this check, not a model problem:** the verification script initially
reused the owner's reference transcript for the *female* reference clip too. `reference_text`
describes what the reference audio says, not the text being synthesized — feeding OmniVoice the wrong
transcript against a different speaker's clip broke cloning outright, producing jibberish. Fixed by
omitting `reference_text` for the female reference (matching how the original arm-Eprod female run
worked — OmniVoice's own Whisper auto-transcribes when it's not supplied). Re-ran; confirmed correct.

**Owner's verdict on the re-listen: numbers now correct in both references, and `owner_04`'s
"میٹنگ" mispronunciation (flagged in §5b as inconsistent — correct in `long_multiclause`, wrong in
`num_ascii`/`num_eastern`) is now also correct in the female clip.** This is consistent with the §5b
hypothesis that the digit-heavy phrasing itself, not the digit glyphs specifically, was dragging the
neighboring word down — expanding the digits to words appears to have resolved both symptoms together.

**Still open, not touched by this fix:** the English-technical-loanword pronunciation issues from §5b
(`URL` → "oo r l", `database`'s Arabic-accented T, `برائے کرم` mispronounced) are a different failure
mode (word-level, not digit-level) and remain unaddressed.

**Not yet shipped to production.** `eval/urdu_numerals.py` is eval-only by the same rule
`urdu_represent.py` follows — nothing here may be imported by `backend/app/` until wiring it into the
real generation path is a deliberate decision, not an eval-script side effect. That wiring (where in
`domain/routing.py`'s transform seam it belongs, whether it's declared in the `route: {transform,
lossy, rationale}` chip per golden rule 5, whether it applies automatically to all Urdu Perso-Arabic
generation or is opt-in) is a real design decision, not made here.

### 5d. Consolidated pronunciation verification model `[LISTEN]` (2026-08-15)

Continuing past §5b/§5c with per-word isolation testing (bare word vs minimal sentence vs full
corpus/synthetic sentence, owner reference, real production `OmniVoiceBackend`, no normalization
applied unless stated) —
`eval/run_isolated_word_checks.py`, `eval/run_database_respell_check.py`,
`eval/run_database_respell_v2.py`, `eval/run_more_checks.py`, clips and listen pages under
`eval/results/urdu_bakeoff/{isolated_word_checks,database_respell_check,database_respell_v2,
more_checks}*`.

**The important conclusion is not "OmniVoice cannot pronounce X."** Across every case tested, the
pattern is the same: **OmniVoice performs well on realistic conversational Urdu, but certain written
representations are poor inputs for synthesis. The frontend can normalize some of these
representations into forms the model handles more reliably.** That is a distinction between model
capability and frontend normalization requirements, not a verdict on the model.

| Category | Status | Basis |
|---|---|---|
| Native Urdu | **PASS** | Ordinary conversational Urdu, including long/multiclause sentences, names, and colloquial text, is generally working well in the tested corpus |
| Numbers/dates — raw digits | **FAIL** | Both ASCII and Eastern Arabic-Indic digits fail identically — the issue is being a digit, not the script |
| Numbers/dates — Urdu word expansion | **PASS / verified on tested cases** | Verified across the original 3 failing corpus items, 5 additional synthetic sentences, both references, with regression checks showing no damage to already-correct ordinary-Urdu cases. Not claimed to be exhaustive over every possible numeric context — verified on the tested corpus and representative synthetic cases only |
| English code-switch in realistic sentences | **PASS** | `office`, `check`, `GitHub`/`pull request`/`review` confirmed working in realistic sentence contexts, both in isolation and in the original corpus |
| Sparse/bare inputs | **UNRELIABLE / evaluation limitation** | Bare single words or 2–3 word fragments are substantially less reliable than realistic full sentences, independent of what they contain (میٹنگ, URL, and database all failed bare; office and check did not). This is recorded as a limitation of testing sparse inputs, not as proof that sparse input explains every individual failure — some failures (numbers, `database`) reproduce in full-sentence context too |
| `URL` | **Targeted respelling verified in the demonstrated realistic failure** | Bare: unreliable/silent. Short sentence: correct. Busy realistic sentence (the original "abbreviations" corpus item): incorrect ("oo r l"). `یو آر ایل` respelling fixes the busy realistic sentence specifically — not claimed to fix every possible `URL` context |
| `database` | ⚠️ **SUPERSEDED by §9c — this row's conclusion was drawn from n=1 and is wrong** | What was recorded here: bare/short forms unreliable; all-Urdu `ڈیٹا بیس` collides with بیس ("twenty"); mixed `ڈیٹا` + Latin `base` "produces the correct pronunciation in the realistic full-sentence case". Blind repeat sampling later scored that mixed form at **7/12** — a coin flip, not a fix — and it shipped for a day on the strength of this single listen. The verified answer is `ڈیٹا بےس` (bari ye), **11/12**. See §9b for why every n=1 verdict in this document is suspect |

**No broad English-transliteration system is being introduced.** office/check/GitHub already work as
plain Latin text; URL and database needed a small, specific respelling each, found and verified
individually — the same one-word-at-a-time, verify-by-ear discipline as every other finding in this
bake-off, not a general rule.

**Still eval-only, nothing wired to production.** Three rules now have eval-verified evidence behind
them:

1. Digit/date expansion (`eval/urdu_numerals.py`)
2. `URL` → `یو آر ایل`, where the demonstrated busy-sentence context requires it
3. ~~`database` → `ڈیٹا` + Latin `base` (mixed script)~~ — **superseded, see §9c.** This is the entry
   that shipped on one listen and later measured 7/12. The verified answer is `ڈیٹا بےس`.

Before any production integration, an open design question remains: do these belong in one general
normalization layer, or a small targeted pronunciation lexicon (closer to what the evidence actually
supports, given how narrowly each fix has been shown to apply)? Not decided here. No further isolated
word testing is planned unless a specific, unresolved, production-relevant failure surfaces — the
current evidence is sufficient to move from exploration to designing the normalization layer itself.

### 5e. Normalization layer shipped to production (2026-08-15)

The open design question from §5d is answered: **a small, per-spec targeted lexicon, not a general
normalization layer** — the evidence never supported "general" (numbers are systematic; URL/database
are two individually-verified words, not a pattern).

`backend/app/domain/urdu_text.py` ports `expand_numbers_in_text`/`_LOANWORD_LEXICON` from
`eval/urdu_numerals.py` into production. Key finding that shaped the design: unlike
`routing.TransformKind` (Perso-Arabic → Devanagari, needs a model, hence the impure
`with_resolved_text()` seam), digit/loanword normalization is pure text manipulation — no I/O — so it
runs synchronously **inside** `resolve()` itself. `TransformKind`/`needs_transform`/
`with_resolved_text()` are untouched.

**Scoped per-spec, not per-language**, the same way `needs_reference_text`/`caveat`/
`experimental_listing` already are: `ModelSpec.text_normalizations` is a new declared field, empty by
default. Only `OMNIVOICE_URDU` sets it — `voxcpm2_urdu_arabic` and everything else stay unaffected,
since the evidence is OmniVoice-specific and was never tested elsewhere. A regression test
(`test_text_normalization_applies_only_to_the_spec_that_declares_it`) asserts exactly this: identical
input routed to two different Perso-Arabic Urdu specs is normalized for one and not the other.

**Visible, per golden rule 5**: `RoutePlan.text_normalizations` reports what was actually applied to
*this* text (not just what the spec declares — a loanword-lexicon miss still reports `()`), and the
route chip's `rationale` names it, e.g. *"...rendered by OmniVoice (Urdu) (numbers normalized to
spoken Urdu words, select English words respelled for pronunciation)"*.

**Verified three ways**, not just unit-tested:
1. 280 backend tests pass (9 new), ruff clean, frontend build clean.
2. A local dry run against the **real catalog** (not stubs) confirmed `omnivoice_urdu` normalizes and
   `voxcpm2_urdu_arabic` does not, on identical input.
3. A real pod run (`eval/run_normalization_layer_check.py`) called the actual `resolve()` against the
   real catalog, fed `plan.resolved_text` to the real `OmniVoiceBackend`, and produced
   `eval/results/urdu_bakeoff/normalization_layer_check/combined_via_resolve.wav` — closing the loop
   between "eval-verified" and "what production actually sends." Not yet independently re-verified by
   ear against that specific clip.

**Decided:** `OMNIVOICE_URDU.verified` flipped to `True` 2026-08-15, the owner's explicit call on top of
the normalization layer landing and the arm Eprod gate clearing comfortably on both references. Being
CC-BY-NC still means it is never picked silently: `ModelCatalog.candidates()` (`inference/catalog.py`)
now excludes non-permissively-licensed specs even once verified, so Auto routing and the `alternatives`
list still skip it — only an explicit `model_id=omnivoice_urdu` request reaches it, same as before, just
without needing `allow_experimental=True` any more. See `test_omnivoice_urdu_is_cc_by_nc_and_verified`
and `test_auto_urdu_routing_still_prefers_no_model_over_an_nc_one` in `test_contracts.py`.

---

## 6. What happens next

1. ~~Blind listening~~ **Done, arms A–E.** 130/130 clips scored across two passes
   (`eval/results/urdu_bakeoff/listen_scores_pass1.json`, 52 clips; `listen_scores_pass2.json`, 78
   clips), aggregated in §3. Two clips have an unresolved playback glitch (§3c) — low priority, the
   underlying audio is fine.
2. **HF token + female transcript** → unblocks arms H/I/J, still open. The `.venv` for it is
   pre-provisioned at `/workspace/engines-lab/r1-f5/` on the pod. **Arm I is the highest-value item
   left** — it is the only thing that can answer questions 1, 3, and 6 fully.
3. **Owner decides**: proceed to integrate a VoxCPM2 arm (C or D, §5) now, or wait for IndicF5 first.
   Nothing is integrated in this repo yet either way.

---

## 7. Reproducing

```bash
# synthesis (per arm, per reference), on the pod, under the GPU lock
flock /workspace/engines-lab/.gpu.lock \
  /workspace/engines-lab/<engine>/.venv/bin/python eval/run_urdu_bakeoff.py \
    --arm C --reference eval/fixtures/voice_urdu.wav --reference-id owner
```

```bash
# scoring — separate venv, deliberately conflicting dependency stacks
flock /workspace/engines-lab/.gpu.lock \
  backend/.venv-eval/bin/python eval/score_urdu_bakeoff.py --device cuda
```

```bash
# blind listening page
backend/.venv-eval/bin/python eval/build_listen_page.py
```

**Traps hit while producing this, worth not re-hitting:**

- `.venv-eval` is at `backend/.venv-eval`, **not** the repo root.
- Do **not** `git merge` on the pod inside `eval/results/` — the newest clips are untracked there and
  the merge aborts (correctly). Check out individual files instead.
- `run_urdu_bakeoff.py` requires **both** `--reference` (path) and `--reference-id` (label).

---

## 8. Phase 2 — transliteration viability probe `[BENCH]`

**Run for real, 2026-08-15, on the pod.** `eval/run_translit_probe.py` (`.venv-qwen`,
`Qwen/Qwen2.5-3B-Instruct`, unpinned — probe-only, golden rule 7 doesn't apply here since nothing
ships from this venv) converts each corpus item's `perso_arabic` and `roman` field to Devanagari and
scores against the **hand-authored gold** `devanagari` field in `eval/fixtures/urdu_corpus.json` —
not a converter's own output, so the score measures the LLM, not a second transliterator's opinion of
itself. Full manifest: `eval/results/translit_probe/manifest.json` (26 cases, 13 items × 2
directions, 0 unparseable).

| Direction | Mean CER | Items | Unparseable |
|---|---|---|---|
| Perso-Arabic → Devanagari | **0.2771** | 13 | 0 |
| Roman → Devanagari | **0.3075** | 13 | 0 |

**owner_core items** (the owner's own 5 sentences — the highest-priority slice):

| Item | Perso-Arabic CER | Roman CER |
|---|---|---|
| owner_01_sick | 0.3485 | 0.2576 |
| owner_02_file | 0.2083 | 0.1458 |
| owner_03_deadline | 0.1636 | 0.2000 |
| owner_04_late | 0.2642 | 0.2642 |
| owner_05_github | 0.4776 | 0.4179 |

**This misses the gate.** The plan's own stated criterion: *"if no candidate gets close to gold on
the `owner_*` items, the transform layer is not built."* A CER of 0.15–0.48 is not close to gold —
for comparison, every VoxCPM2 bake-off arm in §2 scored CER ≤ 0.19 against real audio-to-text ASR
noise, and this probe is text-to-text against a single deterministic model with no audio in the loop
at all. **The predicted direction did not hold either**: Roman was expected to score meaningfully
better than Perso-Arabic (it already writes short vowels, so no abjad→abugida vowel-restoration is
needed) — instead it scored *worse* on 3 of 5 owner items and roughly tied overall (0.3075 vs 0.2771
mean). Spot-checking raw model output against gold (first 6 cases) found errors well beyond simple
vowel restoration: dropped words, semantic substitutions (gold "तक मुकम्मल" → model "से मुक्तिमान"),
and incomplete script-switching within a single token ("फ़ाils", a Devanagari-Latin hybrid). One case
did match the specific failure mode predicted in `routing.py`'s docstring — gold "मुझे" rendered as
"मग्हे", a genuine vowel-restoration miss — but it was one failure mode among several, not the
dominant one.

**What this means for the roadmap, per the gate's own rule:** the transform layer described in the
plan's Phase 2 (`domain/urdu_text.py`, wiring into `tts.py`'s `NoRouteError` seam,
`transliteration_cache`) **should not be built on Qwen2.5-3B-Instruct's single-shot output.** This is
recorded as a finding, the same way arm F (Higgs v3, §1) was recorded as could-not-run rather than
silently dropped. It does **not** close the transliteration route outright — a larger model, few-shot
prompting, or a dedicated (non-LLM) transliterator could still clear the bar; none of those were
tried here. `[INFER]`.

**Reproducing:**

```bash
# on the pod, in .venv-qwen (provisioned by the same flow as pod-bootstrap.sh's other venvs)
.venv-qwen/bin/python eval/run_translit_probe.py
```

### 8b. Retry against the actually-correct target script: Roman → Perso-Arabic `[BENCH]`

**§8 tested the wrong target for OmniVoice specifically.** `OMNIVOICE_URDU`'s catalog cell only claims
`(ur, Script.ARABIC)` — no Devanagari path exists for it at all. §8's probe targeted Devanagari because
that route existed to unlock the VoxCPM2/Hindi-model arms (C/I/J), not OmniVoice. To type Roman and
reach OmniVoice, the actual missing conversion is Roman → **Perso-Arabic**, plausibly easier than
Roman → Devanagari since Perso-Arabic Urdu, like Roman, largely leaves short vowels implicit — no
abjad→abugida vowel-restoration step. Untested until this run.

**Run for real, 2026-08-15, on the pod.** `eval/run_roman_arabic_probe.py`, same model
(`Qwen/Qwen2.5-3B-Instruct`, `.venv-qwen`), same corpus, gold reference is `perso_arabic` directly (the
corpus's own `cer_reference` field, not a derived value). Two prompt variants: zero-shot, and few-shot
using `colloquial`/`technical` as exemplars (excluded from the scored set to avoid leakage). Full
manifest: `eval/results/roman_arabic_probe/manifest.json` (24 scored cases, 12 items × 2 variants).

| Variant | Mean CER | OK | Unparseable |
|---|---|---|---|
| Zero-shot | **0.2332** | 12 | 1 |
| Few-shot | **0.1942** (among parseable) | 9 | 2 |

**owner_core items:**

| Item | Zero-shot CER | Few-shot CER |
|---|---|---|
| owner_01_sick | 0.3125 | unparseable |
| owner_02_file | 0.0833 | 0.1667 |
| owner_03_deadline | 0.2885 | 0.2692 |
| owner_04_late | 0.2830 | unparseable |
| owner_05_github | 0.1642 | 0.1791 |

**This also misses the gate.** Same criterion as §8: not close to gold on the owner_* items, and
few-shot's apparently-better mean is inflated by dropping the two hardest owner items outright rather
than actually improving on them — 2 of 5 owner_core sentences came back with no Perso-Arabic-script
characters at all (the model answered in Latin script, unparseable by design of the harness, same as
§8's parse-validation approach). Perso-Arabic being closer to Roman in vowel representation than
Devanagari did **not** translate into a materially better score than §8's Devanagari attempt (0.23 vs
0.28–0.31) — the errors are not primarily vowel-restoration errors either here (dropped/substituted
words, occasional script refusal), same failure shape as §8.

**What this means:** the `NoRouteError` on Roman Urdu → OmniVoice stays as-is; no transform layer is
being built on this model on either target script. Per this project's own escalation order (cheapest
test first): a bigger model (e.g. `Qwen2.5-7B-Instruct`) and a dedicated non-LLM Roman-Urdu
transliterator both remain untried, and neither is ruled out by this result — this closes out the
Qwen2.5-3B / both-target-script line of investigation, not transliteration as a whole.

**Reproducing:**

```bash
# on the pod, in .venv-qwen
.venv-qwen/bin/python eval/run_roman_arabic_probe.py
```

### 8c. Escalation to a bigger model: Qwen2.5-7B-Instruct `[BENCH]`

**Run for real, 2026-08-15, on the pod.** Same script (`eval/run_roman_arabic_probe.py`, now
parameterized via `PROBE_MODEL_ID` so this reused the exact §8b scaffolding rather than forking a
near-duplicate), same corpus, same two variants. Freed the pod's full 24 GB (an idle backend session
from earlier verification work was resident) before loading — 7B at bf16 is ~15 GB. Full manifest:
`eval/results/roman_arabic_probe_qwen_qwen2_5_7b_instruct/manifest.json`.

| Variant | 3B mean CER | 7B mean CER | 3B unparseable | 7B unparseable |
|---|---|---|---|---|
| Zero-shot | 0.2332 | **0.2693** | 1 | **0** |
| Few-shot | 0.1942 (among parseable) | **0.2029** | 2 | **0** |

**Mixed result, not a clean win.** 7B's mean CER is not better than 3B's — zero-shot is measurably
worse (0.27 vs 0.23) and few-shot is about the same (0.20 vs 0.19). What 7B *does* fix is reliability:
zero unparseable responses in either variant, versus 3 combined for 3B (including 2 of the 5
owner_core items on few-shot, which is exactly the priority slice). All 5 owner_core few-shot items
now score, mean CER ≈0.19 — every one of them still far from "close to gold."

**This also misses the gate**, same criterion as §8/§8b. A bigger model bought reliability
(everything parses) but not accuracy — the errors are not fundamentally different in kind from the 3B
run's, just no longer failing outright on the hardest inputs. This closes out the "try a bigger model"
escalation named in §8b's closing note; the only item left in that note (a dedicated non-LLM
transliterator) was researched and found to have no adoptable option (see docs/ROADMAP.md's
transliteration row). **Both untried escalation paths from §8b are now tried, and both miss the gate.**
Roman Urdu → OmniVoice stays unbuilt.

**Reproducing:**

```bash
# on the pod, in .venv-qwen
PROBE_MODEL_ID=Qwen/Qwen2.5-7B-Instruct .venv-qwen/bin/python eval/run_roman_arabic_probe.py
```

---

## 9. Phase A0 — does OmniVoice read Roman Urdu unaided? `[LISTEN]` **No.**

The cheapest test that could have made the whole Roman→Perso-Arabic conversion feature unnecessary.
`OMNIVOICE_URDU` only *declares* `(ur, ARABIC)`, but nothing had ever fed it Latin — and VoxCPM2
renders Roman Urdu fine, so it was worth ten minutes before building a pipeline.

Eight corpus items, each synthesized twice from one loaded checkpoint against the owner reference:
column **A** from `roman`, column **B** from `perso_arabic` (the *ceiling* — the best any conversion
could deliver). `resolve()` was deliberately bypassed, since routing correctly refuses `(ur, LATIN)`
against an `(ur, ARABIC)` spec and that refusal was the thing under question. Items were chosen with
no bare digits so the absent number normalization could not confound the pair.
Driver `eval/run_a0_roman_direct.py`, clips + page at `eval/results/a0_roman_direct/`.

**ASR screen** (`eval/score_a0_roman_direct.py`, both arms scored against `perso_arabic` per the
corpus's `cer_reference_rule`):

| | mean CER | mean speaker cosine |
|---|---|---|
| A — Roman fed directly | 0.2016 | 0.7171 |
| B — Perso-Arabic gold | 0.1188 | 0.7326 |

The screen looked *encouraging*: the Roman arm is plainly not gibberish. Whisper recovered
near-correct Urdu from it (`owner_02_file` 0.042, `owner_03_deadline` 0.077), and the two worst
items — `owner_05_github` at 0.448 in **both** columns — are a Whisper artifact, since it transcribes
Latin code-switch words phonetically into Urdu script regardless of arm.

**The owner's listen overruled it: "Column A is English-accented."** The model is reading Roman Urdu
as *English orthography*, producing Urdu words in an English accent. CER cannot see this at all — the
words are right, so edit distance is small, while the thing the product is for is wrong. This is the
same lesson as the harness docstring's 2026-08-04 case (cosine 0.686 + "sounds Hindi"), landing a
second time on a different metric.

**A0 fails ⇒ the conversion pipeline is justified.** Phase A continues to A1/A2/A3. No `(ur, LATIN)`
cell is added to `omnivoice_urdu`.

### 9a. What the same listen turned up about column B — `late` and `database`

The owner also reported that **column B**, the supposed ceiling, mispronounces `late` and `database`.
Two different things:

- **`database` — an artifact of how A0 was run, not a production defect.** Feeding text verbatim also
  bypassed `domain/urdu_text.py`'s loanword lexicon, so column B heard raw `database`, which
  production never sends — it sends `ڈیٹا base`, the mixed respelling §5c verified on this exact
  sentence. A0's own control was therefore weaker than production. Worth stating plainly: the ceiling
  measured in §9 is a *floor* on the real ceiling.
- **`late` — real, and a regression against a recorded result.** It is absent from
  `_LOANWORD_LEXICON`, and §5b recorded it as passing (`owner_04_late` ✅, "code-switched
  'office'/'late' both landed"). A word that passed one listen and failed the next is exactly what
  §2's caveats warn about, now demonstrated within this project's own records rather than in the
  abstract.

`eval/run_loanword_late_check.py` puts both, plus `office` as a control, in front of the owner:
`late` as Latin / `لیٹ` / `لیٹھ`, `database` as verbatim / production / all-Urdu. **Pending the
owner's listen** — nothing enters `_LOANWORD_LEXICON` on a hunch, per its docstring.

### 9b. The finding that reframes every listen in this document: **synthesis is unseeded**

Chasing `database` produced something more important than a spelling. On 2026-08-16 the owner
judged `ڈیٹا base` to be "data-boss" (wrong), and about an hour later judged the *same sentence*
correct. The two texts were verified byte-identical from the two manifests — same reference, same
checkpoint, same code path. The only difference was that they were **two separate generations**.

`OmniVoiceBackend.synth()` sets no seed. `self._model.generate(...)` samples freshly on every call,
so **a loanword's pronunciation is a random variable, not a property of the spelling.**

That is not a criticism of the owner's ear; it is a property of the experiment. Every single-listen
verdict in this document is an n=1 draw:

| verdict | where | now reads as |
|---|---|---|
| "late passes, code-switched office/late both landed" | §5b | n=1 |
| "`ڈیٹا base` verified correct in this sentence" | §5c | n=1 |
| "column B mispronounces late" | §9 | n=1 |
| "`ڈیٹا base` is data-boss" / "…is correct" | §9a | n=1 each, and they disagree |

Their disagreement is the *expected* outcome of sampling a coin, not a contradiction needing an
explanation — and it is the simplest account of `late` passing in §5b, failing in §9, then passing
again on the focused re-listen. It also means §5c's method (choose a respelling from one clip) could
not have worked reliably even in principle.

**Consequences, in order of importance:**

1. **The question changes from "is this spelling correct" to "how *often* is it correct".** A
   spelling right 2 times in 4 is not a fix; it is the same coin flip with extra steps.
   `eval/run_loanword_reliability.py` measures this — 8 variants × 4 samples, shuffled under a fixed
   seed and presented as bare numbers, since the owner has now rated identical audio two ways and
   knowing which clip ships is exactly the bias worth removing.
2. **A wrong lexicon entry is worse than none.** Verbatim `database` is merely imperfect;
   `_LOANWORD_LEXICON` actively rewrites it, so a bad entry *introduces* an error for every user.
3. **Users see this variance too.** Even a perfect lexicon leaves any single generation able to come
   out wrong, which makes "regenerate" a real remedy rather than a shrug — and is an argument for
   the editable-text design over any silent transform.
4. **Seeding is worth considering separately** — it would make experiments reproducible, but it also
   freezes one draw, so it must not be added *just* to make this table look stable.

### 9c. `database` settled by blind repeat sampling — and the lexicon's scaling problem

Two blind rounds (`eval/run_loanword_reliability.py`), owner-rated, labels hidden, order shuffled:

| spelling | round 1 (n=4) | round 2 (n=8) | total |
|---|---|---|---|
| `URL` verbatim | 0/4 | — | **0/4** |
| **`یو آر ایل`** (shipped, unchanged) | 4/4 | — | **4/4** |
| `database` verbatim | 0/4 | 0/4 | **0/8** |
| **`ڈیٹا بےس`** (bari ye) | 3/4 | 8/8 | **11/12** ✅ |
| `ڈیٹا base` (shipped until 2026-08-16) | 2/4 | 5/8 | **7/12** |
| `ڈیٹا bays` | 1/4 | 3/8 | **4/12** |
| `ڈیٹا bayss`, `dayta base` | 0/4 | — | **0/4** |

`_LOANWORD_LEXICON["database"]` is now `ڈیٹا بےس`. The verbatim controls scoring **zero** are what
justify having entries at all; the round-2 anchor (verbatim again 0/4) confirms the owner's criteria
did not drift between sessions.

**The result that should change how these are chosen:** `ڈیٹا bays` produced the single best-sounding
clip of the entire experiment — the owner's note was *"most accurate for a native Urdu speaker"* — and
scored **4/12**. Selecting a spelling by its best clip, which is what §5c effectively did, would have
shipped the second-worst candidate. Best-draw and most-reliable are different questions, and only the
second one is the product's.

Note also that the winner is **11/12, not 12/12**. Per §9b there is no spelling that always works,
so "regenerate" is a real remedy for users rather than a shrug.

### 9d. Open design problem: the lexicon is hardcoded and does not scale

`_LOANWORD_LEXICON` is a module-level dict of two entries. A word that is not in it is passed through
verbatim, and adding one costs the owner roughly a dozen blind listens. That is affordable for two
words and not affordable as the general answer to "what happens when a new loanword appears".

What is *not* the answer:

- **A general English→Urdu respeller.** It would have to know which words *these particular weights*
  mispronounce — a property of the model, not of English — so it would guess, and it would damage
  the words that already work (`office`, `check`, `GitHub`, `late`, `backup`, `server`, `restart`).
- **ASR round-trip detection.** Tempting, but §9's screen showed Whisper transcribes Latin
  code-switch words phonetically into Urdu script regardless of whether they were pronounced well —
  `owner_05_github` scored 0.448 in *both* arms. The detector is blind to precisely the failure it
  would need to catch.

The two candidates worth pursuing, in order:

1. **Measure the failure rate first.** Nobody knows how often this actually bites. The expanded
   45-item corpus contains ~20 items carrying Latin islands; synthesizing and blind-rating those
   gives a rate. If it is two words in twenty, a short shipped default list plus the editable
   Composer text box is a complete answer and anything more is over-engineering.
2. **If the rate is material: a user-editable pronunciation dictionary.** Move the mapping out of a
   module constant into per-user data with a small settings UI, and demote the hardcoded dict to
   *shipped defaults*. This scales because the person who cares about a word is the one who fixes it,
   and they only need it to work for themselves — no owner listening session per word. It also fits
   the existing architecture unchanged: still a pure text transform in `domain/urdu_text.py`, still
   applied inside `resolve()`, still no model.

**Not decided here.** Step 1 is cheap and should precede step 2.

### 9e. The loanword failure rate, measured — and what it settles

§9d's open question, answered. 20 Latin-island corpus items × 2 takes, through the **real production
normalization path** (so `database` was already the corrected `ڈیٹا بےس` and `URL` already
`یو آر ایل` — both drop out of the count, making this the *residual* rate for words the lexicon does
not cover). Driver `eval/run_loanword_rate.py`, owner-rated.

| | |
|---|---|
| clips containing at least one bad word | **13/40 (32.5%)** |
| word instances mispronounced | **20/116 (17.2%)** |
| distinct words affected | **11/54 (20.4%)** — one lexicon entry each |

**Not the "2 in 20" that would have made a dictionary over-engineering.** One in three generations
carries a mispronounced English word.

The split between deterministic and stochastic failures is what makes this actionable:

| | words |
|---|---|
| **Always wrong** (2/2 takes) — a spelling fixes these | `message`, `RAM`, `WhatsApp`, `interview`, `asap`, `reply`, `plz`, `API`, `cache` |
| **Sometimes wrong** (1/2 takes) — §9b's unseeded variance, no spelling will fix it | `wait`, `screenshot` |

**Nine of the eleven fail every time.** That is the encouraging half of a discouraging number: these
are systematic, not luck, so a respelling can genuinely fix them and a user who adds an entry gets a
durable result rather than a better coin. The owner's note on `interview` — *"the T is pronounced like
an Arab would"* — is diagnostic of the whole class: the model applies Arabic phonology to Latin
tokens, so `ٹ` (retroflex) is realised as `ت` (dental). That is a phoneme-mapping failure with an
obvious respelling remedy (`انٹرویو`), not a mystery.

Note what the failures are *not*: they are not rare or exotic words. `message`, `reply`, `API`,
`WhatsApp`, `interview` are among the most common English words in Pakistani everyday and workplace
speech. The lexicon cannot be a curiosity shelf.

**Decision: build the user-editable pronunciation dictionary** (§9d's option 2). At 20% of distinct
words, the hardcoded-list-plus-owner-listening approach would require the owner to work through
roughly one word in five of all English vocabulary, at a dozen blind listens each. That does not
finish. Moving the mapping into per-user data with the hardcoded dict demoted to *shipped defaults*
scales because the person who wants a word fixed is the one who fixes it, and it only has to satisfy
them. Architecturally nothing else changes: still a pure text transform in `domain/urdu_text.py`,
still applied inside `resolve()`, still no model, still no `TransformKind`.

Two caveats to carry into that build:

- The 2 stochastic words mean a dictionary **cannot promise correctness**, only improvement. Users
  will still hit a bad draw, so "regenerate" must be a visible remedy (§9b).
- Seeding these numbers is one rater, one reference voice, one corpus, n=2. It is firmly enough to
  decide *build vs don't build*; it is not a per-word verdict, and no word above should be given a
  shipped default spelling without the §9c blind-repeat treatment.
