# Handoff — current state

Written so a fresh session (or a fresh pod, or a different person) can resume without
reconstructing anything. **Update this at every checkpoint.** The previous incarnation of this
project lost a day of planning because the only copy lived on a pod that was terminated.

Last updated: **2026-08-10**, after Speech Direction + Phase 4 Chatterbox scoping merged to `main`.

---

## Where things stand

**The product is complete and validated end-to-end on GPU with real cloned audio, including Speech
Direction's multi-segment generation.** Three bodies of work have landed on `main` since the rewrite:
async jobs/mobile/perf (PR #1), Speech Direction + UI plain-language pass (PR #3), and an IR taxonomy
expansion + Phase 4 Chatterbox design doc (PR #4). Full detail and forward roadmap:
**[docs/ROADMAP.md](ROADMAP.md)**.

| Wave | Status |
|---|---|
| **0 — Contracts** | ✅ Done. |
| **X1 — Deletions** | ✅ Done. −5402 lines, then a further −4628 removing the legacy engines. |
| **1 — Research** | ✅ Done. Catalog holds measured numbers. |
| **B3 — domain layer** | ✅ Done. `detect_script`, `resolve`, `split_sentences`, `chunk_for_synthesis`. |
| **B1 — scheduler** | ✅ Done. GPU-slot invariant asserted; tested against `FakeWorker`, no GPU. |
| **Urdu** | ✅ Concluded — and then superseded: VoxCPM 2 renders **romanized** Hindi/Urdu directly, so the whole transliteration subsystem was deleted. Native Perso-Arabic is still unrouted (422, never mis-rendered). |
| **B2 — API layer** | ✅ Done. Enrollment + consent, generate, media tokens, history (list/get/favorite/delete). |
| **P6 — frontend** | ✅ Done, then revamped 2026-08-06, then a plain-language pass 2026-08-10 (Stability/Pitch/Volume became worded comboboxes, Advanced editing collapsed behind a disclosure — see ROADMAP.md). |
| **P7 — CI/Docker** | ✅ CI green. Dockerfile rewritten CPU-slim but **not build-tested**. |
| **Real-audio E2E** | ✅ Passed on an RTX A4500, then again on an RTX 4000 Ada pod 2026-08-10 (see Speech Direction row). |
| **Jobs/mobile/perf** | ✅ Done, **merged to `main`** via PR #1. Async job queue, TanStack Query + Recent tab, mobile fixes, LCP/FCP/TBT perf. |
| **Speech Direction (Phase 2)** | ✅ **Merged to `main`** via PR #3, then pod-validated for real: enrolled a real voice, ran `apply_direction: true` on multi-sentence Roman Urdu through both `curl` and the live browser UI against a real VoxCPM2 worker — 3 real segments, correct joins (waveform-inspected, no artifacts/clipping), warm RTF ≈1.0. Detail: [ROADMAP.md](ROADMAP.md). |
| **Phase 4 scoping (Chatterbox)** | 📋 **Merged to `main`** via PR #4. `Emotion.ANXIOUS` + `Tone.NARRATIVE` added to the IR (ANXIOUS analyzer-detected now; NARRATIVE deliberately left undetected, documented why). Full Chatterbox design doc: [PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md) — no runtime code yet, that's Phase 4a/4b/4c. |

**217 backend tests passing on `main`** (verified 2026-08-10), ruff clean on everything touched this
session (25 pre-existing findings remain in untouched test files, same as always). Base branch:
**`main`** (the rewrite was merged in on 2026-08-06; `rewrite/contracts` points at the same commit but
has drifted behind — treat as stale until re-synced).

## Resuming on a new pod

The repo is **public for read** — the pod clones anonymously, no token needed. `main` now carries
everything (Speech Direction, the plain-language UI pass, and the Phase 4 IR taxonomy), so the
bootstrap's default branch is correct — no `BRANCH=...` override needed:

```bash
ssh -p <PORT> root@<HOST> "bash -s" < scripts/pod-bootstrap.sh
```

`GH_USER`/`GH_TOKEN` are only needed for pushing commits *from* the pod, not for this clone — see
[POD_SETUP.md](POD_SETUP.md) for the rare anonymous-clone-rejected case.

Rebuilds caches, both venvs (API without torch, runtime **with torch pinned to cu128** — the default
cu130 wheel silently reports `cuda.is_available() == False`), and re-downloads the ~7 GB of weights if
`/workspace` did not carry over. Full runbook: **[POD_SETUP.md](POD_SETUP.md)**.

Note your enrolled voices and history live in `VCS_DATA_DIR` on the pod — they are lost with the
volume, not with the pod.

Connection details for the current pod are in `.claude/remote.local.md` (gitignored — endpoints
change). Note SSH must use Windows `ssh.exe`; Git Bash cannot see the ssh-agent holding the key.

## 🔴 Urdu voice cloning — investigation concluded

Full report: `docs/URDU_CLONING_REPORT.md`. All runs: `docs/PHASE_A_RESULTS.md`. Verdict from the
owner (native Urdu speaker) listening to real output:

**No permissively-licensed zero-shot model clones the owner's voice.** F5, VoxCPM2, and Chatterbox
all produce intelligible Urdu in a *generic* voice. The reason is the finding that matters most:

> **Intelligibility and speaker-identity are independent failures.** Intelligibility was solved
> (Perso-Arabic → Devanagari input fixed CER 0.96 → 0.07). Identity comes from the reference *audio*
> encoder, **not the text** — so transliteration is NOT the voice bottleneck, and no amount of text
> or knob tuning fixes it. Root cause: out-of-distribution speaker encoding (encoders are
> English-trained; a 7 s Pakistani-Urdu voice is off-distribution).

**Path forward:** ship the VoxCPM2 intelligibility pipeline as "a natural Urdu voice" (honest, works,
Apache-2.0); for real cloning, **LoRA fine-tune VoxCPM2** on 2–10 min of the owner's audio. Do NOT
try more zero-shot models or samplers — the ceiling is the encoder.

**Closed — do not re-investigate:** F5 vocab (Devanagari, 0 Arabic chars), EMA, nuqta-folding,
zero-shot knobs, transliteration-as-voice-cause. All ruled out with evidence in the report.

Ranked best→worst by ear: `out_voxcpm_urdu_deva.wav` > `out_chatterbox_standard.wav` >
`out_chatterbox_maxref.wav`. All in `C:\Users\abdus\Downloads\Voices\`.

### Reusable durable assets (now in git, not on a pod)

- `eval/eval_harness.py` — Whisper large-v3 CER + ECAPA-TDNN cosine + RTF. **The gate is a SCREEN,
  not a verdict** (VoxCPM2 passed CER, nearly passed cosine, still sounded like a stranger). Needs a
  torch venv: `uv pip install torch torchaudio transformers speechbrain jiwer soundfile`.
- `eval/fixtures/voice_urdu.wav` — the owner's reference (6.67 s), with transcript + Devanagari
  transliteration + standard target sentence in `eval/fixtures/README.md`.

### Lessons carried forward

**1. Verification means execution.** R2 produced a report with three ❌ in its own summary table and
concluded "READY TO SHIP: All critical deliverables verified" — having never loaded the model.
Treat any research result claiming verification without a command transcript as unverified. The
plan's top-listed bad idea is Wave 3 implementing against documentation instead of verified
snippets, and this is exactly how that happens.

**2. `df` DOES NOT SHOW THE VOLUME QUOTA. Use `du -sh /workspace`.**

R2 reported "disk quota exceeded". That was dismissed on the basis of `df -h /workspace` reporting
164 TB free — but `/workspace` is a MooseFS mount, and `df` reports the whole **cluster**, not this
volume's quota. The volume was 50 GB and actual usage was ~49.3 GB:

```
23.0 GB  uv-cache
 5.0 GB  hf-cache
 1.2 GB  pip-cache
20.1 GB  engines-lab venvs (r1 4.5 + r2 3.3 + r3 8.9 + r4 3.4)
-------
49.3 GB  vs a 50 GB volume
```

R2's diagnosis was correct and the dismissal was wrong. The volume has since been raised to 200 GB.

**Check capacity with `du -sh /workspace` against the volume size shown in the RunPod console.**
Never with `df`. A wrong reading here sends you debugging dependency resolution for an hour when the
actual failure is "out of space".

Budget note: the uv cache alone reached 23 GB across four runtimes. Four ML stacks plus weights fit
in 200 GB, but not comfortably in 50 GB — `uv cache prune` is worth running between waves.

## Measured pod facts (2026-08-04)

- RTX A5000, 24564 MiB, sm_86, driver 580.159.04 · Ubuntu 24.04.3 · Python 3.12.3 · torch 2.8.0+cu128
- `/` = 30 GB **ephemeral** overlay (4.6 GB/s) · `/workspace` = MooseFS network volume (526 MB/s)
- Network: **~7 MB/s from HuggingFace, ~16 MB/s from PyPI.** Wave 1's wall clock is weight
  downloads, not compute — budget accordingly.
- Present: git, ffmpeg, uv, flock. Missing: **node, npm** (so frontend work happens locally), nvcc,
  espeak-ng (not needed by any chosen runtime).

## What's left to build

The backend-rewrite waves (B1–B3, P6, P7) above are all done — that list is stale, kept for
history. **Current priorities live in [docs/ROADMAP.md](ROADMAP.md)**, not here:

- Merge `feature/speech-direction` to `main` (open a PR — none exists yet as of 2026-08-10).
  `feature/jobs-mobile-perf` is already merged (PR #1) and deleted.
- **Pod validation of directed generation** (the immediate next step — see below): confirm real
  VoxCPM2 audio through the multi-segment path, no clicks/discontinuities at segment joins.
- The Qwen2.5-Instruct LLM analyzer (Apache-2.0, pod-only) behind the frozen `analyze()` signature.
- Phase 2 remainder: the Advanced per-segment IR editor (preview + apply-checkbox shipped; full
  editing did not).
- Phase 3: client-side audio extraction (move video→audio out of the backend, ffmpeg becomes the
  fallback for formats `decodeAudioData` can't handle, not the only path).
- Phase 4 (undesigned): Chatterbox runtime — catalog already declares its `exaggeration` param
  ([catalog.py:155](../backend/app/inference/catalog.py)), no runtime implements it yet. Given the
  Urdu report below, VoxCPM2 stays the cloning-quality priority regardless.
- **D1** — Docker, CI. Dockerfile rewritten CPU-slim but still **not build-tested**.

## Non-negotiables (full detail in CLAUDE.md and docs/ARCHITECTURE.md)

1. `import torch` must not be reachable from `app.main`. Enforced by
   `test_no_torch_outside_runtimes`. Check by hand with **leading whitespace allowed** — the legacy
   engines import torch inside functions, so an anchored grep reports them clean while the invariant
   is broken.
2. Eviction only inside `_ensure_ready()`, only while holding the GPU-slot semaphore.
3. Routing is pure — no `is_loaded`, ever. That is what made a cold server answer with a sine wave.
4. No silent fallback. `NoRouteError` → 422 listing what would work.
5. Permissive licenses only.
6. Nothing routes until Phase A verifies it. `LanguageSupport.verified=False` is the default and the
   catalog currently resolves nothing — deliberately.

## Design facts established by research (in the catalog / code already)

1. **Two F5 loader paths, not one class.** IndicF5 → `AutoModel(trust_remote_code=True)`;
   OpenBible-Urdu → raw checkpoint via stock `f5-tts` loader.
2. **`f5_openf5_en` dropped** — no permissive English F5 exists (all derive from CC-BY-NC SWivid).
   English routes to Chatterbox.
3. **F5 reference limit ~12 s, silent truncation** (not ~6 s; "8192" is an unrelated rotary table).
4. **F5 blank `ref_text` silently loads Whisper** — always pass `ref_text`.
5. **VRAM must be sampled concurrently** — post-hoc readings under-report peak ~5×. Scheduler sizes
   from recorded `vram_mb`, not live readings.
6. **~~Roman Urdu → Devanagari via `ai4bharat-transliteration`~~ — dead, do not reintroduce.**
   VoxCPM2 renders romanized Hindi/Urdu directly (tokenizer-free, owner-verified by ear); the whole
   transliteration subsystem was deleted. `ai4bharat` was also a py3.12 dependency nightmare
   (fairseq → tensorflow_addons → keras3 breakage, 9.8GB venv).
7. **VoxCPM2 warm-up trap** — built-in warm-up skips the cloning path; first real clone eats +40–55 s
   unless warmed with a real reference.

## Next session — start here

Everything through Phase 4 *scoping* is merged and pod-validated. Two real options, not sequenced —
pick based on what's wanted next:

1. **Phase 4a (Chatterbox capability/render mapping, CPU-only, no GPU needed)** — land
   `_CHATTERBOX_FIELDS` + the exaggeration/cfg_weight blend table in `app/jobs/direction.py`, the
   `language_id` plumbing change, and the associated tests (including repointing
   `test_direction_capability.py`'s `_NON_VOXCPM` fixture off of Chatterbox — a known, planned
   breaking change, see the design doc). Full spec: [PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md)
   §5 and §11. Buildable and testable entirely on this box.
2. **Qwen2.5-Instruct analyzer** (the "analyzer" half of the earlier "both, analyzer first"
   decision) — pod-only, needs the model download and a GPU worker to keep torch out of the API
   process. See [ROADMAP.md](ROADMAP.md)'s Phase 2 "Remaining" list.

A background task is independently auditing `direction_analyze.py`'s existing lexicon for the same
`\b` + Devanagari-combining-mark bug found and worked around while adding `ANXIOUS` (see PR #4's
commit message) — check its result before adding more lexicon entries by hand.

**Token discipline (owner priority):** terse replies, no recaps, no exploratory pod runs without
go-ahead, batch verification. Build inline when holding the contracts (B1/B3 were faster+cheaper that
way than spawning agents). Only spawn agents for genuinely parallel work.

## Open items

- [ ] **Rotate the GitHub PAT** (`ghp_...`) — pasted into the transcript, and written to two pods'
      `/root/.git-credentials`. Permanently logged.
- [ ] **Urdu product decision (owner):** ship generic-voice MVP, or invest in LoRA fine-tune of
      VoxCPM2. See `docs/URDU_CLONING_REPORT.md` §4.
- [ ] Accept the IndicF5 HF license + set `HF_TOKEN` on the pod (unblocks `f5_indic`).
- [ ] Empty `LEGACY_TORCH_IMPORTERS` once the old engine layer is deleted (blocked on B1/B2/B3
      landing replacements — deleting it now would break the running app).
- [ ] `NOTICE` file with CC-BY-SA attribution for OpenBible-Urdu.

Resolved since last check (2026-08-09): `SettingsPage.tsx` no longer exists (the desktop shell was
dropped project-wide) and `main.py` has no `tauri://` CORS origins — both stale items removed from
this list. `frontend/src-tauri/` itself (leftover generated files, no Tauri config anywhere) was
removed from git and gitignored the same day.
