# Urdu bake-off — results

**Status: INCOMPLETE. No model has been chosen.** Synthesis and automated screening are done for
every arm that can run; the **blind listening pass has not happened yet**, and it is the decision.
Arms H/I/J are blocked on a token. Nothing in this file names a winner, and nothing here may set
`LanguageSupport.verified`.

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

## 3. The two axes stay separate

Per the plan, these are **never collapsed into one verdict**, and both are `[LISTEN]`-pending:

| A. Urdu quality | B. Voice cloning |
|---|---|
| Pakistani pronunciation — *pending* | speaker identity vs reference — *pending* |
| Urdu vocabulary — *pending* | consistency across sentences — *pending* |
| phonology — *pending* | male-reference performance — *pending* |
| naturalness, prosody — *pending* | female-reference performance — *pending* |
| Urdu-English code-switching — §4 | |

Explicitly: transliteration will **not** be dismissed for failing to improve speaker identity, and
**not** accepted because pronunciation improves while the voice becomes a different person.

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

## 5. Decision table — **empty on purpose**

Filled only after the blind listening pass. Leaving it blank is the point: a table filled from §2
would be exactly the mistake this plan was restructured to avoid.

| Candidate | Urdu | Pakistani Urdu | Voice cloning | Naturalness | Code-switch | Speed | VRAM | Commercial | Decision |
|---|---|---|---|---|---|---|---|---|---|
| VoxCPM2 (roman / A) | | | | | | 0.76 RTF | 6.4 GB | 🟢 | |
| VoxCPM2 (perso-arabic / B) | | | | | | 0.76 RTF | 6.4 GB | 🟢 | |
| VoxCPM2 (devanagari / C) | | | | | | 0.85 RTF | 6.4 GB | 🟢 | |
| VoxCPM2 + LoRA (D) | | | | | | 0.94 RTF | 6.5 GB | 🟢 | |
| OmniVoice (E) | | | | | | 0.44 RTF | 4.5 GB | 🔴 NC | |
| Higgs v3 (F) | — | — | — | — | — | — | — | 🔴 NC | **could not run** |
| IndicF5 (H/I/J) | | | | | | | | 🟡 gated | **blocked on token** |

### The 9 questions

| # | Question | Answer |
|---|---|---|
| 1 | Best model for Pakistani Urdu, by listening? | ⏸ **no listening yet** |
| 2 | Best commercially safe option? | ⏸ candidates are VoxCPM2 A/B/C/D only (E and F are NC, IndicF5 has an unresolved provenance question) — but *which* is unanswerable without listening |
| 3 | Can IndicF5 produce Pakistani Urdu via Devanagari/phonetic conversion? | ⏸ **blocked** — arms I/J never ran |
| 4 | Native Urdu vs transliteration? | ⏸ arms B vs A/C are synthesized and unheard. §2a shows the screening numbers do **not** settle it |
| 5 | Does VoxCPM2 + LoRA meaningfully improve Urdu? | ⏸ arm D vs B synthesized, unheard. `[BENCH]` shows D's cosine is the *lowest* of every VoxCPM2 arm at both references (0.662 / 0.689), consistent with the identity regression already recorded in `docs/VOXCPM_LORA_POC.md` — but cosine is the untrustworthy metric here |
| 6 | Which model should we integrate? | ⏸ **nothing is being integrated yet** |
| 7 | Should we add an Urdu transformation layer? | ⏸ depends on whether arm C or I beats arm B by ear. Note `normalize_urdu` is a **no-op on 13/13 corpus items**, so ladder rung B is untestable with this corpus regardless |
| 8 | What stays the English backend? | **VoxCPM2** — unchanged and not under test here. The owner's assessment is that English is already good, and nothing in this bake-off touches it |
| 9 | Best future fine-tuning strategy? | ⏸ the identified *commercially permissive* route to investigate is VoxCPM2 (Apache-2.0) on UrduSpeech (CC-BY-4.0, Pakistani, 57% code-switched). **A route to investigate, not a validated solution** — nothing has been trained on it or heard |

Question 8 is the only one answerable today, and only because it is answered by *not* changing
anything.

---

## 6. What happens next

1. **Blind listening** — `eval/results/urdu_bakeoff/listen.html`. 130 clips, opaque tokens, mapping
   in `key.json` (do not open until finished). 5 criteria × 1–5 + free-text per clip. Export to
   `listen_scores.json`.
   *Verified working*: 156 embedded MP3 data URIs (130 clips + 26 reference copies), zero relative
   audio paths, and an in-browser check confirming `play()` advances `currentTime` with
   `error === null`. The previous build failed with `MediaError` code 4.
2. **HF token + female transcript** → unblocks arms H/I/J. The `.venv` for it is pre-provisioned at
   `/workspace/engines-lab/r1-f5/`.
3. **Then, and only then**, fill §5 and decide.

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
