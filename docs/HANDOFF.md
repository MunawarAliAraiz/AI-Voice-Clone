# Handoff — current state

Written so a fresh session (or a fresh pod, or a different person) can resume without
reconstructing anything. **Update this at every checkpoint.** The previous incarnation of this
project lost a day of planning because the only copy lived on a pod that was terminated.

Last updated: **2026-08-12**, mid-session, ahead of a context/usage limit — two background agents
(Qwen analyzer production backend, VoxCPM2 LoRA POC) are still running; see "In flight right now"
below before assuming anything about them.

---

## Where things stand

**The product is complete and validated end-to-end on GPU with real cloned audio, including Speech
Direction's multi-segment generation.** Base branch is **`main`**, currently at commit `777a82a`.
Landed since the rewrite: async jobs/mobile/perf, Speech Direction (preview → multi-segment audio →
backend contract for edits → full Advanced per-segment editor UI), Phase 4 Chatterbox — designed,
built, gated, and **concluded not shippable** (see below), a Composer model picker, and client-side
audio extraction. Full detail and forward roadmap: **[docs/ROADMAP.md](ROADMAP.md)**.

| Area | Status |
|---|---|
| **Core rewrite (Waves 0/1/B1-B3/P6/P7)** | ✅ Done, stable. Not touched this session. |
| **Async jobs / mobile / perf** | ✅ Done, merged. |
| **Speech Direction (Phase 2)** | ✅ **Fully landed on `main`.** Heuristic analyzer + capability report + preview UI + multi-segment generation + client-edited per-segment override contract (`direction_plan` on `TTSGenerateRequest`, sparse/index-keyed, re-validated server-side, 422 on stale index) + the full Advanced per-segment IR editor UI (editable emotion/intensity/energy/rate/pause per segment). **Real-audio pod validation done 2026-08-12**: hit `POST /api/generate` with `apply_direction: true` against a live VoxCPM2 worker, downloaded the actual output, automated waveform check found zero click-threshold discontinuities and silence runs landing exactly at the expected segment boundaries — objectively sound, human listen still open. Clip at `eval/results/direction/pod_directed_hi.wav`. |
| **Qwen2.5-Instruct LLM analyzer** | ✅ **Production backend merged to `main` (2026-08-12)**. `QwenAnalyzerBackend`, `AnalyzerScheduler`, `JobKind.ANALYZE_LLM`, `POST /api/direction/analyze-llm`. Real pod-verified: direct backend, full worker-subprocess path, and the real HTTP path all passed clean on a fresh pod (0 problems, en/ur/hi) after a genuine bug (`load_time_sec` not threaded through) was found and fixed. 239 backend tests, ruff clean. **Frontend wiring not built** — no UI calls the endpoint yet. Known open risk: idle-unload timer is the only VRAM-contention mitigation vs. the audio scheduler, documented in `analyzer_scheduler.py`, not resolved. |
| **Phase 4 (Chatterbox)** | 🔴 **Designed, built, gated, and concluded NOT shippable.** Real `ChatterboxBackend`, real Phase-A gate run, real human listen. Owner's verdict: "not that good... identity is matched around 60%". Same failure shape as the Urdu investigation below — a speaker-encoder ceiling, not a tunable parameter. Not planned to be revisited without a LoRA fine-tune (see next row). |
| **VoxCPM2 LoRA POC** | 🚧 **Just kicked off, running in the background right now** on branch `feature/voxcpm-lora-poc` (own worktree, `D:\Projects\voxcpm-lora-poc-worktree`). This is the identified path to real identity-preserving cloning. A real 36-clip (~4.8 min) dataset of the owner's own voice already exists, untracked, at `eval/training/` — **do not commit the raw audio without explicit owner sign-off**, that decision was deliberately left open. Early finding: the installed `voxcpm` package (2.0.3) has genuine first-class LoRA support built in (`voxcpm/modules/layers/lora.py`, `LoRAConfig`, a `voxcpm.training` submodule) — better-supported than expected going in. See "In flight right now". |
| **Composer model picker** | ✅ Done, merged. Explicit model override + "(Recommended)" hint, no Tone control (confirmed no-op). |
| **Client-side audio extraction (Phase 3)** | ✅ Done, merged. |

**256 backend tests passing** as of the last full run this session (`feature/phase2-advanced-direction`
before its merge), ruff clean on everything touched. `gh` CLI is now installed locally (`winget install
GitHub.cli`), so PRs can be opened directly going forward instead of handed over as links.

## In flight right now (2026-08-12) — check these before doing anything else

One background agent is still mid-run as of this checkpoint; the other finished and is merged.
**Don't assume the running one is done, and don't duplicate its work** — check for a completion
notification / the branch's actual git log first.

1. **Qwen2.5 LLM analyzer production backend — DONE, merged to `main` (commit `2be3759`).** Built
   `WireOp.CLASSIFY`, `QwenAnalyzerBackend` under `inference/runtimes/`, `AnalyzerScheduler` (a
   torch-free sibling to `InferenceScheduler`, not a `scheduler.py` edit), `JobKind.ANALYZE_LLM`,
   `POST /api/direction/analyze-llm`, an idle-unload timer. Pinned HF revision
   `aa8e72537993ba99e69dfaafa59ed015b17504d1`. Verified for real on a fresh pod (the original pod died
   mid-build, this is a genuine redo not a reused result): direct backend, full worker-subprocess
   path, and the real HTTP path all passed with 0 problems across en/ur/hi. Found and fixed a real bug
   (`load_time_sec` wasn't threaded from LOAD into the following classify call) during verification.
   239 backend tests passing (reverified independently after merge, not just trusted from the agent's
   report), ruff clean. **Frontend wiring is NOT built** — that's the next real piece of this feature.
   **Open risk, not resolved**: the idle-unload timer is the only VRAM mitigation between this
   scheduler and the audio one; they don't share a real budget.
2. **VoxCPM2 LoRA POC** — branch `feature/voxcpm-lora-poc`, worktree at
   `D:\Projects\voxcpm-lora-poc-worktree` (this one was **not** spawned with worktree isolation by
   mistake — it briefly checked out a branch directly in the shared `D:\Projects\AI-Voice-Clone`
   working tree, built on the wrong `origin` remote, and clobbered the main checkout mid-edit. It
   self-corrected: found the `fork`-vs-`origin` mismatch on its own, and moved to the separate worktree
   after being told to. See the new lesson below — **always pass `isolation: "worktree"` when a
   subagent needs its own git branch**, no exceptions, even for "quick" tasks). Scope: validate the
   dataset, research whether `voxcpm`'s built-in LoRA support has a runnable training entrypoint or
   needs a training loop written against its primitives, run a minimal POC fine-tune if feasible,
   evaluate with the same `eval_harness.py` methodology used for Chatterbox's gate, write
   `docs/VOXCPM_LORA_POC.md`. Told explicitly not to commit the raw training audio without sign-off,
   not to touch `backend/app/`, and not to claim `verified` on a cosine number alone.

When either finishes: review its diff before merging (same pattern used this session for the frontend
Advanced editor — it was cherry-picked onto the backend contract branch after a build+test check, not
merged blind), run the full test suite, and update this file + `docs/ROADMAP.md` again.

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

**Current priorities live in [docs/ROADMAP.md](ROADMAP.md)**, not here. As of this checkpoint:

- The two in-flight background agents above — review, merge, verify once they report back.
- Once the Qwen analyzer backend lands: frontend wiring (a trigger in `DirectionPanel.tsx`/
  `Composer.tsx` to call `POST /api/direction/analyze-llm` and let the user apply its suggestions,
  same shape as the existing heuristic preview but async/job-polled). Not started, not designed yet.
- Once the LoRA POC reports back: either a go/no-go on real production integration (how a fine-tuned
  adapter would load into `VoxCPMBackend`), or — if it's a no-go — the Urdu product decision below
  becomes live again (ship generic-voice MVP as the final answer, or look at another path).
- **D1** — Docker, CI. Dockerfile rewritten CPU-slim but still **not build-tested**.
- PR housekeeping: `main` currently has no open PRs; `gh` CLI is installed locally now, so future
  branches can get a real PR instead of a handed-over compare link.

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

1. **Check on the two in-flight agents first** (Qwen analyzer backend, LoRA POC — see "In flight right
   now" above). If either finished mid-session-boundary, its work is sitting on its own branch/worktree
   unreviewed — read the diff, run tests, merge or send it back with feedback before starting anything
   new.
2. If both are genuinely done and merged: Qwen analyzer frontend wiring, or the LoRA POC's production
   integration (if it was a go), are the next real pieces of work. See "What's left to build" above.
3. **New operational lesson, read before spawning any subagent that needs its own git branch:**
   **always pass `isolation: "worktree"`.** This session forgot it once (the LoRA POC agent) and it
   checked out a branch directly in the shared `D:\Projects\AI-Voice-Clone\` working tree, clobbering
   an in-progress edit mid-session. No exceptions for "this one's quick."
4. **Second lesson: `origin` in this local repo is not `fork`.** `origin` →
   `IftikharAhmedDev/AI-Voice-Clone.git` (an unrelated, stale predecessor fork — no rewrite, no
   `docs/ROADMAP.md`, contains code this project's own `CLAUDE.md` says was deleted, e.g. "Style
   Exaggeration"). `fork` → `MunawarAliAraiz/AI-Voice-Clone.git`, the real one. Always fetch/push/branch
   off `fork`, never `origin`, in this repo specifically. Two different subagents hit this same trap
   independently this session (the frontend Advanced-editor agent, and the LoRA POC agent) — it is not
   a one-off, it's this repo's actual remote configuration. Tell every future subagent this explicitly
   rather than assuming they'll discover it themselves.

**Token discipline (owner priority):** terse replies, no recaps, no exploratory pod runs without
go-ahead, batch verification. Build inline when holding the contracts. Use subagents for genuinely
parallel/independent work (this session ran two GPU-pod agents concurrently, plus a frontend UI agent
earlier) — but isolate every one of them in a worktree, and give each one the correct `fork` remote
explicitly rather than assuming it'll figure out which remote is real.

## Open items

- [ ] **Rotate the GitHub PAT** (`ghp_...`) — pasted into the transcript, and written to two pods'
      `/root/.git-credentials`. Permanently logged.
- [ ] **Urdu product decision (owner):** in progress, not resolved — the LoRA-fine-tune path is now
      being probed (see "In flight right now"), not just proposed. See `docs/URDU_CLONING_REPORT.md` §4.
- [ ] Accept the IndicF5 HF license + set `HF_TOKEN` on the pod (unblocks `f5_indic`).
- [ ] Empty `LEGACY_TORCH_IMPORTERS` once the old engine layer is deleted (blocked on B1/B2/B3
      landing replacements — deleting it now would break the running app).
- [ ] `NOTICE` file with CC-BY-SA attribution for OpenBible-Urdu.

Resolved since last check (2026-08-09): `SettingsPage.tsx` no longer exists (the desktop shell was
dropped project-wide) and `main.py` has no `tauri://` CORS origins — both stale items removed from
this list. `frontend/src-tauri/` itself (leftover generated files, no Tauri config anywhere) was
removed from git and gitignored the same day.
