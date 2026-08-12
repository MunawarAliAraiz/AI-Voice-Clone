# VoxCPM2 LoRA fine-tuning — feasibility probe

> **Status: IN PROGRESS, BLOCKED ON POD ACCESS.** Dataset validated, feasibility established with
> high confidence, training manifests prepared, training run planned and configured — but not yet
> executed. The pod (`157.157.221.29:24549`) went unreachable (`connection refused` / `connection
> timed out`, six retries over ~2.5 min) immediately after the dataset was uploaded and before the
> actual `train_voxcpm_finetune.py` run could start. No checkpoint exists yet. This document will be
> updated with real before/after numbers once the pod is back and the run completes — until then,
> every claim below is either a citation to a specific file/command already executed, or explicitly
> marked as planned-not-run.

## 1. Problem statement

Every voice-cloning result recorded so far in this project (`docs/PHASE_A_RESULTS.md`,
`docs/PHASE4_CHATTERBOX_DESIGN.md`) shares one finding: zero-shot cloning — VoxCPM2 or Chatterbox,
either architecture — reliably produces intelligible, on-language speech, but does not reliably
preserve the *specific* speaker's identity. VoxCPM2's own Hindi cell scored a passing-looking CER
(0.0702) with a speaker cosine that a native-speaker listen overturned ("sounds Hindi, not like
him"); Chatterbox's owner-verdict was "identity is matched around 60%". Both investigations concluded
the same thing: this is a **speaker-encoder ceiling**, not a knob to tune — the zero-shot conditioning
path was never going to bake in more identity than a few seconds of reference audio can carry. The
next thing on record to try, per both investigations, is **LoRA fine-tuning VoxCPM2 on the owner's
own voice** — not more sampling, not more zero-shot parameter sweeps.

This document is the first attempt at that. It is scoped as a probe: validate that this is actually
buildable before treating it as a production feature, in the same spirit as how Chatterbox went
probe → design doc → real gate → honest human-verified verdict before this project called anything
"done".

## 2. Dataset — validated

`eval/training/` (untracked in git — see §6): 36 clips of the repo owner's own voice speaking Urdu,
recorded and transcribed for exactly this purpose. Same person, same consent framing as
`eval/fixtures/voice_urdu.wav` (`eval/fixtures/README.md`).

Verified directly (`wave` module, all 36 files):

| | |
|---|---|
| Clip count | 36 |
| Total duration | **288.17 s = 4.80 min** (the task brief's "roughly 4 minutes" estimate was low) |
| Per-clip range | 3.89 s (`clip_17.wav`) – 11.83 s (`clip_10.wav`) |
| Format | **16000 Hz, mono, 16-bit PCM** — uniform across all 36 files, no outliers |

Two things worth flagging, neither a blocker:

- **16 kHz, not `voice_urdu.wav`'s 24 kHz.** This is a non-issue for training specifically —
  VoxCPM2's `AudioVAE` encoder input rate is 16 kHz (`docs/PHASE_A_RESULTS.md`: "16 kHz encoder input,
  48 kHz output"; independently reconfirmed below, §3), so the training manifest's 16 kHz *is* the
  rate the encoder wants, no resampling step needed. It only matters if the same 36 clips are ever fed
  through `eval_harness.py`'s ECAPA speaker-similarity check directly (that model wants 16 kHz too and
  resamples automatically — also fine).
- **Manifest text is Devanagari (`manifest.jsonl`, ASR-generated); `transcripts_review.tsv`'s
  `perso_arabic_ur` column is the human-reviewed, more accurate transcript in real Urdu script.** This
  document uses `perso_arabic_ur` for training text — see §4 for why that's now an empirically
  justified choice, not just a preference.
- No silence-only or clipped clips found by inspection of the duration/format scan; a full waveform
  listen pass was not done (out of scope for a text-processing validation pass — flagged, not claimed
  clean).

Against the project's own documented VoxCPM2 LoRA guidance (§3), 36 clips / 4.8 min sits squarely
inside "single speaker cloning: 5–50 clips" and each clip's duration sits inside the recommended
"3–30 seconds per clip" — this dataset was, whether by luck or design, already shaped correctly for
this exact use case.

## 3. LoRA/PEFT feasibility — CONFIRMED, with a caveat about *where* the confirmation comes from

The instruction going in was not to assume `peft`-library LoRA injection works out of the box. It
doesn't need to, because it isn't used — **VoxCPM2 ships its own first-class, built-in LoRA
implementation**, not a `peft`-wrapped one. Verified directly against the exact package installed in
`backend/.venv-voxcpm` on the pod (`voxcpm==2.0.3`, the same install `VoxCPMBackend`
(`backend/app/inference/runtimes/voxcpm.py`) uses in production):

- **`voxcpm/modules/layers/lora.py`** — a hand-written `LoRALinear(nn.Module)` that wraps an
  `nn.Linear`, holds `weight`/`bias` directly (so pretrained state-dict keys need no translation) plus
  `lora_A`/`lora_B` parameters, and computes `base_linear(x) + dropout(x @ A^T @ B^T) * scaling`.
  `apply_lora_to_named_linear_modules()` walks a module tree and swaps in `LoRALinear` wherever a
  named submodule (e.g. `q_proj`, `v_proj`) matches a target list.
- **`voxcpm/model/voxcpm2.py`** — `class LoRAConfig(BaseModel)`: `enable_lm` / `enable_dit` /
  `enable_proj` (apply to the text-semantic LM, the local-DiT decoder, or the projection layers,
  independently), `r=8`, `alpha=16`, `dropout=0.0`, and explicit target-module name lists
  (`target_modules_lm`/`target_modules_dit` default to `[q_proj, v_proj, k_proj, o_proj]`;
  `target_proj_modules` to `[enc_to_lm_proj, lm_to_dit_proj, res_to_dit_proj, fusion_concat_proj]`).
  `VoxCPM2Model._apply_lora()` wires this in at construction time when a `lora_config` is passed, and
  the class exposes `load_lora_weights()`, `get_lora_state_dict()`, `set_lora_enabled()`,
  `reset_lora_weights()` — full lifecycle management, not just injection.
- **`voxcpm/core.py`** (the same `voxcpm.core.VoxCPM` class `VoxCPMBackend.load()` already
  instantiates in production) already accepts `lora_config` and `lora_weights_path` kwargs in both
  `__init__` and `from_pretrained`, plus `load_lora()` / `unload_lora()` / `set_lora_enabled()` /
  `get_lora_state_dict()` / `lora_enabled` delegating methods. **The production wrapper class already
  has every hook needed to load a LoRA checkpoint** — today's `VoxCPMBackend` just doesn't pass
  `lora_weights_path` through yet (expected: that's a separate, later, production-wiring decision, not
  in scope here — see §7).
- **`voxcpm/training/`** — a real training toolkit, not stubs: `accelerator.py` (a hand-rolled
  `Accelerator` mirroring the shape of HuggingFace `accelerate` — AMP, gradient scaling, DDP prep,
  gradient-accumulation `no_sync()` — with zero dependency on the actual `accelerate` package),
  `data.py` (`load_audio_text_datasets()` — loads a JSONL manifest via HF `datasets`, casts the audio
  column to a target sample rate, is intentionally column-name-flexible), `packers.py`
  (`AudioFeatureProcessingPacker` — turns raw audio + text into the packed multimodal token sequence
  the model trains on, via the model's own `AudioVAE`), `tracker.py` (TensorBoardX-backed metric
  logging), `validate.py` (a pre-flight **manifest validator** — checks file existence, sample rate,
  audio readability, before any GPU time is spent), `state.py`.
- **The `voxcpm` console script** (`entry_points.txt`: `voxcpm = voxcpm.cli:main`, confirmed installed
  in `backend/.venv-voxcpm/bin/voxcpm`) already exposes `--lora-path` / `--lora-r` / `--lora-alpha` /
  `--lora-dropout` / `--lora-disable-lm` / `--lora-disable-dit` / `--lora-enable-proj` on `design`,
  `clone`, and `batch`, plus a standalone `voxcpm validate --manifest <jsonl>` subcommand.

**The caveat:** none of the above — the model-level LoRA support, the `voxcpm.training` library
code — is enough by itself to *run* a fine-tune. It's a library, not a script. The actual runnable
entrypoint, **`scripts/train_voxcpm_finetune.py`**, is **not part of the PyPI wheel** (confirmed:
absent from `voxcpm-2.0.3.dist-info/RECORD` on the pod) — it only exists in the GitHub source repo.
Cloned to `/workspace/engines-lab/voxcpm-lora/VoxCPM_repo_src` (shallow clone,
`OpenBMB/VoxCPM@616d3d3`, 2026-07-08) to get it, alongside the reference configs
`conf/voxcpm_v2/voxcpm_finetune_lora.yaml` and `conf/voxcpm_v2/voxcpm_finetune_all.yaml` (LoRA vs.
full fine-tune) and two more test scripts (`test_voxcpm_ft_infer.py`, `test_voxcpm_lora_infer.py`).

**Version-compatibility check, done before trusting any of this:** diffed the repo's
`src/voxcpm/model/voxcpm2.py` and `src/voxcpm/training/data.py` against the exact files installed by
`voxcpm==2.0.3` in `backend/.venv-voxcpm`. `training/data.py` is byte-identical. `voxcpm2.py` differs
only in main-branch-ahead inference-time seed-retry bookkeeping (`materialize_generation_seed`,
`apply_generation_seed`, a `last_successful_seed` field) — nothing in the `LoRAConfig` class, the
training forward path, or the training submodule. **Confirmed safe to run the repo's training script
directly against the pip-installed package**, no reinstall needed. All of the training script's
imports (`argbind`, `datasets`, `tensorboardX`, `safetensors`) are already present in
`backend/.venv-voxcpm` (`uv pip list` confirmed: `argbind 0.3.9`, `datasets 3.6.0`,
`tensorboardx 2.6.5`, `safetensors 0.8.0`, `torch 2.8.0+cu128`) — **zero additional installs required**,
satisfying "reuse `.venv-voxcpm`'s exact install" cleanly rather than needing a venv-local addition.

**Official documentation** (`https://voxcpm.readthedocs.io/en/latest/finetuning/finetune.html`)
corroborates the manifest schema found in `voxcpm/training/data.py` (`audio`, `text` required;
`ref_audio`, `duration`, `dataset_id` optional) and gives sizing guidance directly relevant to this
dataset:

| Goal | Recommended data | This dataset |
|---|---|---|
| Single speaker cloning | 5–50 clips, LoRA | **36 clips** ✅ |
| Domain/style adaptation | 50–500 clips | — |
| New language | 500+ hours, full FT | — |
| Clip duration | 3–30 s | **3.89–11.83 s** ✅ |

VRAM guidance from the same page: VoxCPM2 LoRA ≈ 20 GB at `batch_size=16`/`max_batch_tokens=8192`; full
fine-tune ≈ 40 GB. **This matters concretely here**: the pod has 20475 MiB total and, per §5, a
concurrent background job was holding ~6.3 GB of it — so the reference config's `batch_size=16` does
not fit and the actual run (§5) uses a much smaller batch with gradient accumulation instead.

## 4. Script question — RESOLVED: VoxCPM2 does not require Devanagari

The brief asked this explicitly because `eval/fixtures/README.md` states "every model tested so far
requires Devanagari input" (true of F5-TTS-OpenBible-Urdu — its vocab is 59 Devanagari entries, 0
Arabic, confirmed in `docs/PHASE_A_RESULTS.md`) and prior project memory said VoxCPM2 "renders Roman
Hindi/Urdu directly" without settling Perso-Arabic specifically. `backend/app/inference/catalog.py`'s
`VOXCPM2` spec comment already states VoxCPM2 "is tokenizer-free and renders ROMANIZED Hindi/Urdu
directly" (verified 2026-08-05) — but that verification covers Latin-script Hindi/Urdu and Devanagari
Hindi, not real Perso-Arabic Urdu script, which is what `transcripts_review.tsv`'s ground-truth column
actually contains.

Resolved empirically, cheaply, on CPU, no model load needed — loaded just the tokenizer
(`VoxCPM2Tokenizer`, a `LlamaTokenizerFast`/SentencePiece wrapper, **not** a closed per-language
vocabulary like F5's) from the exact pinned production checkpoint
(`openbmb/VoxCPM2@bffb3df5a29440629464e5e839f4d214c8714c3d`, the same revision
`catalog.py`'s `VOXCPM2.hf_revision` pins) and tokenized the same sentence in three scripts:

| Script | Chars | Tokens | `<unk>` count | Tokens/char |
|---|---|---|---|---|
| Perso-Arabic Urdu (`perso_arabic_ur`) | 87 | 127 | **0** | 1.46 |
| Devanagari Hindi (ASR `devanagari_hi`) | 93 | 168 | **0** | 1.81 |
| Roman Urdu | 69 | 32 | **0** | 0.46 |

**Zero unknown-token fallback on real Perso-Arabic Urdu** — the tokenizer is genuinely multi-script,
not Devanagari-gated. It's also *more* token-efficient on Perso-Arabic than on the Devanagari ASR
transcript of equivalent content (1.46 vs 1.81 tokens/char), so there's no efficiency argument for
preferring Devanagari either. Roman remains the most efficient by a wide margin, consistent with the
already-verified production finding.

**Decision, and why it's the right one independent of the tokenizer result:** train on
`transcripts_review.tsv`'s `perso_arabic_ur` column — the human-reviewed, accurate transcript — rather
than `devanagari_hi`, the uncorrected Whisper ASR pass that `transcripts_review.tsv` itself flags with
`uncertain_hi_words` on several rows (e.g. `clip_01.wav`'s ASR pass mis-hears "asalamu alaikum" as
"असलामुलेकुम" — visibly wrong even before checking the flag column). Training text quality directly
sets the text→audio alignment signal quality; there was already a reason to prefer the correct
transcript, and the tokenizer probe removes the only reason not to.

`eval/prepare_voxcpm_lora_manifest.py` (committed, this branch) builds the training manifest from this
column. Sample size for the probe: **train.tsv 32 clips / val 4 clips** (`clip_33`–`clip_36` held out
for the validation split — enough for the training script's built-in val-loss + audio-preview logging
without meaningfully starving the tiny training set).

## 5. Training run — COMPLETED on a second pod (2026-08-12), real numbers below

**Update:** §5 below (the "PREPARED, NOT YET EXECUTED" account) describes the *first* pod attempt,
which died mid-setup before training started, as documented. The project's pod was recreated
(`157.157.221.29:22080`, GPU fully free this time — no concurrent job holding VRAM, unlike the first
attempt's constraint), and training was re-run from scratch there: dataset re-uploaded (36 clips
verified present), manifests regenerated (a Git-Bash path-mangling bug was hit and fixed in the
process), both train/val manifests passed `voxcpm validate` as a pre-flight check, then
`scripts/train_voxcpm_finetune.py` was launched under the GPU flock, nohup'd/backgrounded so it would
survive the SSH session.

**Real measured results, from the actual training log (not estimates):**
- **Speed:** steady-state ~2.3-2.4 s/step (23-24s per 10-step logging interval). First 10-step
  interval was 64.17s (one-time CUDA/compile warmup). One isolated outlier interval at step 210 took
  50.51s — no OOM, no error, training continued normally immediately after.
- **Total time:** all 300 steps completed in **~15 minutes wall clock**, including 4 validation
  checkpoints (steps 0/100/200/299) each with 2-sample audio generation, which is folded into that
  per-step average.
- **`loss/stop`** dropped cleanly from 0.039 to ~0.00003-0.0001 — the model picked up this speaker's
  utterance-length/stop-token behavior fast.
- **`loss/diff`** stayed noisy in the 0.93-1.12 band throughout, no clear downward trend — expected
  for 74 epochs over only 32 training clips, and **not itself informative about identity quality**
  (that's what §6's eval step measures, not training loss).
- **No crash, no OOM.** GPU sat around 17.6/20.5 GB throughout.
- **Checkpoints saved** at `/workspace/engines-lab/voxcpm-lora/checkpoints/poc_lora/` on the (now-dead)
  pod: `step_0000000`, `step_0000100`, `step_0000200`, `step_0000299`, `step_0000300`, and `latest/`.
- **Rescued before the pod died again:** the `latest/` checkpoint (`lora_config.json`,
  `lora_weights.safetensors` — 72 MB, `training_state.json`) was copied off the pod to
  `eval/results/voxcpm_lora/checkpoint_backup/` on this Windows machine. **`lora_config.json` and
  `training_state.json` are committed to this branch; `lora_weights.safetensors` itself is NOT** —
  `.gitignore:23` excludes `*.safetensors` project-wide (matching this project's "never commit model
  weights" convention, same reason VoxCPM2/Chatterbox weights are re-downloaded from HF rather than
  versioned). The actual weights file exists ONLY on this local disk right now, nowhere else. If this
  machine's copy is lost before a future session picks this up, retraining is cheap (~15 min measured
  above, not a real setback) — but it is not currently backed up anywhere, which is worth knowing
  before assuming it'll still be there.

**What follows below (the original "PREPARED, NOT YET EXECUTED" account) is kept for the record of
the first attempt's config decisions, which the second run mostly reused as-is** (same LoRA
r=32/alpha=32 on LM+DiT, same `sample_rate: 16000`, same manifest structure) — only the pod, the GPU
contention situation, and `batch_size`/`grad_accum_steps` tuning differed run-to-run.

### First attempt (superseded) — PREPARED, NOT YET EXECUTED (blocked on pod access)

Plan, config decided, not yet run:

- **Location:** `/workspace/engines-lab/voxcpm-lora/` on the pod — own directory, not the repo tree,
  per the project's engine-lab convention. `VoxCPM_repo_src/` (shallow clone, for `scripts/` + `conf/`
  only) and `dataset/training/` (the 36 wav files, scp'd directly — confirmed present, 13 MB, 36 files,
  before the pod dropped) live there.
- **Venv:** reuses `backend/.venv-voxcpm` directly (§3) — no new venv needed, nothing installed into
  it, nothing in `backend/` touched.
- **Manifests:** `eval/training/manifest_lora_train.jsonl` (32 entries) / `manifest_lora_val.jsonl` (4
  entries), generated locally by `eval/prepare_voxcpm_lora_manifest.py`, pointing at
  `/workspace/engines-lab/voxcpm-lora/dataset/training/wav/clip_NN.wav` — prepared, **not yet copied to
  the pod** (the pod dropped between preparing them and the `scp`).
- **Config**, adapted from the repo's reference `conf/voxcpm_v2/voxcpm_finetune_lora.yaml` (§3) for
  this pod's actual constraints:
  - `pretrained_path`: the local snapshot `snapshot_download(repo_id="openbmb/VoxCPM2",
    revision="bffb3df5a29440629464e5e839f4d214c8714c3d")` resolves to
    (`/workspace/hf-cache/hub/models--openbmb--VoxCPM2/snapshots/bffb...` — confirmed present, already
    cached).
  - `sample_rate: 16000` — matches both the dataset (§2) and the AudioVAE encoder's asserted rate
    (`voxcpm2.py`'s `base_model.audio_vae.sample_rate`; the training script hard-asserts this matches
    or refuses to start).
  - `batch_size: 1`, `grad_accum_steps: 8` (effective batch 8) — deliberately far below the reference
    config's `batch_size=2`/`grad_accum_steps=8` (effective 16), because the reference's ~20 GB VRAM
    estimate assumes the whole card; **the pod had a concurrent background job (a different LLM
    fine-tune/inference task, per the coordinating brief) holding ~6.3 GB of the 20475 MiB card** at
    last check, leaving roughly 14 GB free. Smaller batch first, watch `nvidia-smi` under `flock
    /workspace/engines-lab/.gpu.lock`, raise it only if headroom allows.
  - `lora: {enable_lm: true, enable_dit: true, enable_proj: false, r: 32, alpha: 32, dropout: 0.0}` —
    the reference config's defaults; no reason to deviate for a first probe run.
  - `num_iters` / `max_steps`: a few hundred (not the reference's 1000) — this run's purpose is
    proving the pipeline produces a loadable checkpoint with a measurable cosine delta, not chasing a
    production-quality result. Exact count to be tuned once the actual per-step wall-clock time on this
    card is known (not yet measured).
  - `save_path` / `tensorboard`: under `/workspace/engines-lab/voxcpm-lora/checkpoints/` and
    `/logs/` — never inside the repo tree.
- **GPU coordination:** every GPU-touching step (the tokenizer probe already run in §4 was CPU-only
  and did not need the lock; the actual training run will) wrapped in
  `flock /workspace/engines-lab/.gpu.lock`, per `CLAUDE.md`. `nvidia-smi` checked immediately before
  planning the batch size (§ above) — 6300 MiB used by
  `/workspace/AI-Voice-Clone/backend/.venv-voxcpm/bin/python` (pid 3620, 0% utilization — idle-resident,
  not actively computing), presumed the concurrent job mentioned in the brief. Not killed, not touched.

**What actually happened:** the dataset upload (`scp -r eval/training` → pod, 13 MB, 36 files)
completed and was verified present. Manifests were generated locally. Immediately after, the pod
stopped answering SSH (`connection refused`, then `connection timed out` — six retries with backoff
over ~2.5 minutes) before the manifests could be copied over, before `voxcpm validate --manifest
manifest_lora_train.jsonl` could run as a pre-flight check, and before `train_voxcpm_finetune.py`
started. **No training has run. No checkpoint exists.**

## 6. Evaluation — STARTED, INTERRUPTED before real numbers landed

**Status as of this checkpoint (2026-08-12):** §5's training run completed successfully and a real
checkpoint exists (`eval/results/voxcpm_lora/checkpoint_backup/`). The baseline-vs-LoRA comparison
below was started on the second pod — but the pod was shut down (a planned, scheduled shutdown, not a
crash — see `docs/HANDOFF.md`'s pod-deadline note) before it produced scored numbers. **No CER/cosine/
RTF numbers exist yet for either the baseline or the LoRA checkpoint.** `eval/run_voxcpm_lora_eval.py`
was written and is ready to run (in this branch), but was never executed to completion. This is
exactly the kind of infrastructure interruption `docs/HANDOFF.md`'s "screenshot not verdict" discipline
exists for: report what actually happened, not what was expected to happen. **The training success in
§5 is real and independently valuable (proves the pipeline works end-to-end and produces a loadable
checkpoint); it is NOT a stand-in for the identity-cosine numbers this whole POC exists to produce.**

**Next session: resume from the existing checkpoint, do not retrain.** The rescued checkpoint at
`eval/results/voxcpm_lora/checkpoint_backup/` is everything needed to run §6 to completion without
touching the training pipeline again — get a pod, load `VoxCPM` with and without the LoRA weights
applied, synthesize the standard target sentence both ways, score both with `eval_harness.py`, and
fill in real numbers here.

Planned, per the brief, once a checkpoint exists: reuse `eval/eval_harness.py` unmodified, the same
reference (`eval/fixtures/voice_urdu.wav`) and the same standard target sentence
(`eval/fixtures/README.md`) already used for every other before/after comparison in this project.
Concretely:

1. Baseline: `VoxCPMBackend`-equivalent zero-shot synth (`reference_wav_path=voice_urdu.wav`, no LoRA)
   of the standard target sentence — the existing recorded number to compare against is
   `docs/PHASE_A_RESULTS.md`'s VoxCPM2 Hindi Devanagari cell (CER 0.0702, speaker cosine 0.6859) and/or
   `catalog.py`'s more recent verified numbers (cosine 0.887 for Hindi Devanagari, but against a
   public-domain reference, not the owner's voice — not the same comparison; **the owner-identity
   comparison must use `voice_urdu.wav` specifically**, matching the original Hindi-cell methodology).
2. LoRA: the same call, `lora_weights_path` pointed at the trained checkpoint's `latest/` folder,
   same reference, same target sentence, same `cfg_value`/`inference_timesteps` defaults — isolates
   the LoRA delta from everything else.
3. Score both with `eval_harness.py` (CER, ECAPA speaker cosine, RTF), on the same `.venv-eval` stack
   used for the Chatterbox Phase 4c gate (torch/transformers/speechbrain/jiwer/soundfile — this venv
   does **not** currently exist on this pod instance; it needs to be rebuilt ad hoc, same as Phase 4c,
   since `pod-bootstrap.sh` still doesn't script it — noted there as a gap, not new here).
4. Commit the generated clip (both baseline and LoRA versions) to `eval/results/voxcpm_lora/` for the
   same human-listen step every other numeric result in this project has needed before being called
   anything more than "worth listening to" (`eval_harness.py`'s own docstring — "a PASS here means
   'worth a human listening to it'. It does not mean the output is usable.").

None of this has happened yet.

## 7. Open questions for the owner

- **Commit the training dataset?** `eval/training/wav/*.wav` (36 clips, ~13 MB, the owner's own voice)
  is currently untracked and has **not** been committed or pushed, per the explicit instruction not to
  decide this. Same reasoning as `eval/fixtures/README.md`'s consent writeup for `voice_urdu.wav`
  applies (same person, same stated purpose) — but 36 clips is a larger volume of biometric data than
  the one 6.67 s clip already in the repo, and that's a bigger call than this probe should make
  unilaterally. `manifest.jsonl`, `transcripts.txt`, `transcripts_review.tsv`, and the
  `manifest_lora_{train,val}.jsonl` files this document's script generated all contain the transcript
  text (not just paths) and are held to the same standard — also not committed.
- **Production wiring, if the eventual numbers justify it:** `VoxCPMBackend.load()`
  (`backend/app/inference/runtimes/voxcpm.py`) does not currently accept or pass `lora_config` /
  `lora_weights_path` through to `voxcpm.core.VoxCPM`, even though the underlying class already
  supports it (§3). That's a real but separate decision — not touched here, per the brief's explicit
  instruction to leave `backend/app/` alone. If this probe's numbers end up good enough to pursue, the
  wiring itself looks small (the hooks already exist); the harder open question is *which* LoRA
  checkpoint would ship, how it would be distributed/loaded, and whether one fixed LoRA (one specific
  identity) even fits this product's model, which serves many different users' voices from the same
  base checkpoint today.

## 8. Recommendation (interim — will be revised once §5–6 complete)

**Do not treat this as a negative result.** Nothing found so far argues against LoRA fine-tuning
VoxCPM2 — quite the opposite: the architecture has first-class, well-documented LoRA support built by
the model's own authors (not a bolt-on), a real training script exists and was version-checked
compatible with the exact production package, the dataset is validated and sits inside the officially
recommended range for exactly this use case, and the one open technical question (script choice) came
back with a clean, unambiguous answer. The only reason this document doesn't yet have real before/after
numbers is the pod going unreachable mid-session, which is an infrastructure interruption, not a
finding about feasibility.

**Next session should:**
1. Reconnect (or re-provision — check `.claude/remote.local.md` for whatever pod address is current)
   and re-verify `/workspace/engines-lab/voxcpm-lora/` and `/workspace/AI-Voice-Clone` survived (the
   dataset upload lives under `/workspace`, which has survived a pod termination before but is not
   guaranteed to).
2. `scp` `eval/training/manifest_lora_{train,val}.jsonl` (regenerate via
   `eval/prepare_voxcpm_lora_manifest.py` if `/workspace` did not survive — the source data is still on
   this Windows machine either way) to the pod, run `voxcpm validate --manifest
   manifest_lora_train.jsonl` as a pre-flight check before spending any GPU time.
3. Run `scripts/train_voxcpm_finetune.py` per §5's config, under the GPU lock, watching `nvidia-smi`
   against whatever else is resident on the card.
4. Run §6's evaluation exactly as specified — same harness, same reference, same target sentence,
   directly comparable numbers.
5. Update this document with the real numbers and a real recommendation. Do not flip any
   `LanguageSupport.verified` flag or call this production-ready regardless of the numbers — that
   needs the same human-listen step every other result in this project has needed.
