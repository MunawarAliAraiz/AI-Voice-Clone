# Handoff — current state

Written so a fresh session (or a fresh pod, or a different person) can resume without
reconstructing anything. **Update this at every checkpoint.** The previous incarnation of this
project lost a day of planning because the only copy lived on a pod that was terminated.

---

## ⚡ Start here (state as of 2026-08-15, end of day)

**Everything below is merged to `main`.** No branch is outstanding. The last four PRs (#18–#21)
covered: the Urdu pronunciation normalization layer, full Hindi removal + Direction-preview model
passthrough + language-based model pre-select, the `OMNIVOICE_URDU.verified=True` flip + the
licence-aware auto-routing filter, and the 7B transliteration escalation.

**What a fresh session should do first:**
1. **The pod is dead.** `69.30.85.171:22002` stopped responding mid-session (connection refused,
   RunPod reclaimed it). Bootstrap a new one — `scripts/pod-bootstrap.sh` now provisions the Qwen
   analyzer venv too (it was missing for three days after that feature shipped), so a fresh pod is
   fully capable including the "Let AI suggest emotion/tone" button. Update
   `.claude/remote.local.md` with the new endpoint.
2. **Three things wait on the owner's ears, not on code**: the two never-scored bake-off clips
   (§3c), the multi-segment directed-audio clip (`eval/results/direction/pod_directed_hi.wav`,
   local + untracked, passed every objective check but never perceptually confirmed), and — once a
   pod exists again — a re-run of `eval/run_user_report_check.py`, whose output was lost with the
   pod before it could be downloaded.
3. **IndicF5 (arms H/I/J) is still blocked**, and the blocker is now precisely known: the HF token
   supplied on 2026-08-15 authenticates fine (`whoami: munawaraliaraiz`) but the account has **not
   been granted access to the gated `ai4bharat/IndicF5` repo** — fetching weights returns
   `403 … you are not in the authorized list`. One click at huggingface.co/ai4bharat/IndicF5 while
   signed in as that account fixes it; nothing else about this arm can proceed until then.

Historical detail on how the current state came to be follows.

---

## LoRA withdrawn, OmniVoice shipped, code-switch bug fixed (2026-08-15)

Originally branch **`feature/urdu-bakeoff`**, since merged. The bake-off itself (130/130
blind-scored, arms A–E) was already complete as of the previous checkpoint below, and arm D (VoxCPM2
+ LoRA) had been integrated as `voxcpm2_urdu_lora`. **This checkpoint reverses that** based on real
usage, then ships the bake-off's actual best-scoring arm properly.

**1. The LoRA was withdrawn.** Using the real running app (not the eval harness), the owner's verdict
was that **base VoxCPM2 sounds better than the LoRA** — contradicting the blind-listen median (4.0 vs
3.0). Per this project's own "owner listening is authoritative" rule, that overrides the earlier
score. `voxcpm2_urdu_lora` is deleted from the catalog (LoRA runtime plumbing — `lora_local_path` etc.
on `VoxCPMBackend`/`ModelSpec` — is kept; it's generic and free when unused). Replaced by
`voxcpm2_urdu_arabic`: the same base checkpoint, no fine-tune, `experimental_listing=True`,
`verified=False`, so Perso-Arabic Urdu isn't left with zero routes. `docs/URDU_BAKEOFF_RESULTS.md` §5a
records the withdrawal honestly, including that this is consistent with Q5's original finding that the
LoRA's gain may have come from matching arm C's representation rather than the fine-tune itself.

**2. Two real bugs found and fixed while testing in the app:**
   - The model picker rendered `ModelSpec.notes` (a maintainer field — env var names, doc paths) raw
     as a user-facing sentence. Added `ModelSpec.caveat: str` (≤140 chars, tested), the only
     user-facing prose field; `Composer.tsx` now renders one line, not a wall of text. Trimmed further
     after the owner flagged even the first version as "too much text."
   - **Code-switched Urdu was unroutable.** `میں نے GitHub پر ایک نیا pull request بھیجا ہے` (Latin
     loanwords inside Perso-Arabic Urdu — UrduSpeech's own corpus is 57% code-switched) measured under
     the 0.85 script-dominance threshold and was rejected as `AmbiguousScriptError` before routing ever
     ran, despite every model handling code-switching fine once it got there. Fixed in
     `domain/language.py`'s `profile_text()`: for a language with a native non-Latin script, Latin runs
     under `LATIN_ISLAND_CEILING = 0.75` are now treated as loanword islands, not ambiguity.
     `detect_script()` itself is untouched (needed pure elsewhere). **Confirmed live** — see below.

**3. OmniVoice integrated as a new runtime** (`RuntimeKind.OMNIVOICE`,
`backend/app/inference/runtimes/omnivoice.py`) — the bake-off's actual best-scoring arm (5.0/5
pronunciation, the only Urdu cell whose CER+cosine gate passes on both references), CC-BY-NC
licensed. Golden rule 6 in `CLAUDE.md` was amended: NC weights are now allowed in the catalog **for
personal use behind `VCS_API_KEY`**, badged (`ModelSummary.commercial_use`), never for a shipped
product. `RESEARCH_ONLY` (Higgs Audio v3's tier) stays fully banned. Real pod smoke test against the
production `OmniVoiceBackend` (not the eval harness): load 159.3s, synth 17.1s (5.48s audio, peak
0.7573 — non-silent), clean unload. Found a real architectural detail along the way: OmniVoice
lazily loads an embedded Whisper sub-model on the *first* `synth()` call (not during `load()`) when
no `ref_text` is supplied, adding latency beyond the reported load time.

**4. Live end-to-end verification, real pod backend + local frontend (2026-08-15), just completed
before this pod is terminated.** Backend run via `serve.sh` on the pod (port 8000), tunneled to the
local machine over SSH (`ssh -L 8000:127.0.0.1:8000`), local Vite dev server (port 1420) proxying
`/api` through the tunnel — no ngrok needed since the API key requirement was dropped for this
private-tunnel test only (never touched `vcs-secrets.env`). Confirmed in the real running app:
   - The picker shows the new catalog cleanly: `VoxCPM 2`, `VoxCPM 2 (Urdu, اردو script)
     (Experimental)`, `OmniVoice (Urdu) (Experimental, Non-commercial)` — `voxcpm2_urdu_lora` only
     survives in old history rows, not as a selectable model.
   - Manually selecting OmniVoice and generating real Perso-Arabic Urdu against a real enrolled voice
     **worked end-to-end through the actual job queue** — `route.rationale` correctly read "ur in
     arabic script rendered by OmniVoice (Urdu) — EXPERIMENTAL: you explicitly picked this model..."
   - **The code-switch fix is confirmed live, not just in tests**: a fresh sentence with English
     loanwords (`میں نے GitHub پر ایک نیا pull request بھیجا ہے، امید ہے آج ہی review ہو جائے گا۔`)
     resolved to `source_script: arabic` and routed to OmniVoice successfully — previously this would
     have 422'd as `AMBIGUOUS_SCRIPT` before reaching routing at all.
   - **Roman Urdu → OmniVoice does NOT work, by design, not yet by gap.** `omnivoice_urdu`'s catalog
     entry only declares an `(ur, ARABIC)` `LanguageSupport` cell — no `(ur, LATIN)` cell. Per
     `routing.py`'s `resolve()`, an explicit model request is honored or refused, never silently
     swapped, so Roman Urdu text with OmniVoice explicitly selected raises `NoRouteError` (422). This
     is exactly what Phase 2 (the transliteration viability probe, not yet started — see below) exists
     to potentially unlock; it is not a bug in what shipped.

**5. Pod is being terminated by the owner right after this checkpoint.** A fresh pod will be needed
next session — run `pod-bootstrap.sh` against this branch (it now provisions `.venv-omnivoice` too,
steps 10-11). Everything from this checkpoint is committed and pushed to `fork/feature/urdu-bakeoff`
(`2634372` at time of writing) — nothing was left pod-only.

**Still open, unchanged from before:** IndicF5 (arms H/I/J) blocked on `HF_TOKEN`. Also still open:
Phase 3 (IndicF5 arms H/I/J), Phase 4 (fine-tune VoxCPM2 on UrduSpeech, largest, not started).

**Resolved since:** the production CER/cosine gate re-run (arm Eprod) cleared comfortably on both
references, and the owner flipped `OMNIVOICE_URDU`'s `(ur, ARABIC)` cell to `verified=True` on
2026-08-15 on top of that. It is still CC-BY-NC, so `ModelCatalog.candidates()`
(`inference/catalog.py`) now excludes non-permissively-licensed specs even once verified — Auto routing
still never reaches it, only an explicit `model_id=omnivoice_urdu` request does (and no longer needs
`allow_experimental=True` to do so). Phase 2 (transliteration viability probe via Qwen2.5-3B) ran twice
— Devanagari target (§8) and, since OmniVoice's own cell is Perso-Arabic not Devanagari, a Perso-Arabic
target retry (§8b) — both missed the gate. Full detail: `docs/URDU_BAKEOFF_RESULTS.md` §5d/§8/§8b.

**Root-cause finding that reframed the work:** VoxCPM2's published list is 30 languages including
Hindi and Arabic but **not Urdu**, and its card says it infers language from the text. So Roman Urdu
likely gets Hindi phonotactics and Perso-Arabic may get *Arabic* phonology — possibly worse. That is
`[INFER]`, not measured; arms A/B/C exist to settle it.

**Also corrected:** a prior pass dismissed IndicF5 for not listing Urdu. Wrong — Hindi and Urdu are
one spoken language differing mainly in script, so a missing language label constrains the *input
representation*, not necessarily the phonetic capability. `docs/URDU_CLONING_REPORT.md` §2 ruled
transliteration out as a fix for **speaker identity**; it never tested it for **pronunciation**.
Those axes are now scored separately.

| Piece | State |
|---|---|
| `tts.py` transform-path bug | ✅ Fixed + regression test. Called `GenerationError(detail)` against a 2-arg `__init__`, so the "fail loudly" branch raised `TypeError`. Now a clean 422 `NoRouteError`. Reachable via `urdu_strategy="translit"`. 244 tests pass. |
| `eval/fixtures/urdu_corpus.json` | ✅ 13 items — owner's 5 sentences + numbers (ASCII/Eastern as a controlled pair), dates, names, acronyms, technical terms, colloquial Pakistani, long multi-clause. |
| `eval/urdu_represent.py` | ✅ `strip_nuqta` (derives arm I from arm J), `normalize_urdu`, `to_ascii_digits`. |
| `eval/run_urdu_bakeoff.py` | ✅ Synthesis, one arm per invocation. |
| `eval/score_urdu_bakeoff.py` | ✅ Scoring, runs in `.venv-eval`. |
| `eval/build_listen_page.py` | ✅ Blind listening page, verified in-browser. |
| `docs/URDU_MODEL_LICENSING.md` | ✅ Full report, code-vs-weights checked separately. |
| `docs/URDU_BAKEOFF_RESULTS.md` | ✅ Written, **§5 decision table deliberately empty** until the listen. |
| Arms A/B/C/D (VoxCPM2 ×3 repr + LoRA) | ✅ 13 items × 2 references each. Scored. |
| Arm E (OmniVoice) | ✅ 13 items × 2 references. Scored. Lightest arm: 4.5 GB, 0.44 RTF. |
| Arm F (Higgs v3) | ⛔ **could_not_run, recorded with the reason** — see below. |
| Arm G (code-switch) | ✅ Reporting slice over A–E. Numbers are a **metric artifact**, see results §4. |
| Arms H/I/J (IndicF5) | ⏸ **Blocked on an `HF_TOKEN`** + the female transcript. venv pre-built at `/workspace/engines-lab/r1-f5/`. |
| Blind listen | ✅ **130/130 clips scored (arms A–E), one rater.** See `docs/URDU_BAKEOFF_RESULTS.md` §3. |

**Arm F is genuinely impossible on this pod, not skipped.** transformers 5.15.0 has no
`higgs_multimodal_qwen3`; `config.json` has `auto_map: null` so `trust_remote_code` can't rescue it;
the only documented self-hosting path is the `lmsysorg/sglang-omni:dev` Docker image and Docker is
not installed (a RunPod container can't nest one); and mainline pip `sglang` has zero Higgs models
(0 GitHub code-search hits). Boson lists ≥40 GB as known-good, 24 GB as unverified. **Don't retry
this without either Docker or a ≥40 GB card.** `_load_higgs` now preflights and raises with that
whole diagnosis, which the harness records as `could_not_run`.

**Arms H/I/J need one click from the owner.** `ai4bharat/IndicF5` is `gated=auto` — metadata reads
fine anonymously, but `model.safetensors` (1.4 GB) returns `GatedRepoError: 401`. Because the whole
repo is gated, `trust_remote_code` can't fetch the shipped `f5_tts/` modules either. `gated=auto`
means approval is **automatic on accepting the terms**, so this is a token, not an application.
Arm I is the plan's central question, so this is the highest-value unblock available. The `.venv` at
`/workspace/engines-lab/r1-f5/` is already built (f5-tts, vocos, torchdiffeq, transformers 5.15.0,
torch 2.13.0+cu130) — running arm I is one command once the token lands.

**Blind listening is done for arms A–E — 130/130 clips, one rater (the owner).** Full breakdown in
`docs/URDU_BAKEOFF_RESULTS.md` §3–5; headline findings:
- **Devanagari input (arm C) and the LoRA (arm D) both beat Roman/Perso-Arabic VoxCPM2 (A/B) by a
  full point on pronunciation and naturalness** (4.0 vs 3.0), tied on identity and code-switch. This
  is the strongest *commercially clean* result.
- **OmniVoice (arm E) rated highest on pronunciation (5.0)** but worst on code-switch (3.0) — and is
  NC-licensed, so it's evidence about the ceiling, not a deployable answer.
- **The LoRA's `docs/VOXCPM_LORA_POC.md` cosine regression did not survive to the ear** — blind
  identity scores were flat at 4.0 for every arm including D. Trust the listening score over the
  automated cosine here; this is exactly the divergence the two-axis design exists to catch.
- **The corpus's number items read digits as digits, not spoken Urdu number-words** — the owner
  flagged this independently on 4 clips across 3 unrelated arms (A/C/D), which is the signature of a
  shared input problem, not a per-model one. Not fixed; `eval/fixtures/urdu_corpus.json`'s
  `date`/`num_ascii`/`num_eastern` items need spelled-out Urdu numerals before their next use.
- Two clips (`C/female/num_eastern`, `D/owner/owner_02_file`) reported as unplayable in the browser;
  both underlying WAVs checked directly and are **not silent** (peak 0.99 / 0.92) — a page playback
  glitch on those two specific clips, not a synthesis defect. 1.5% of the corpus, unresolved, low
  priority.

**No model has been chosen for integration.** The commercially-clean leaders are arms C and D
(tied). Questions 1, 3, and 6 in the results doc stay open until arm I runs or the owner explicitly
defers it — do not integrate anything off the back of this listening pass alone.

**Devanagari is hand-authored gold, deliberately.** Arms C/I/J are therefore a **ceiling test**: if a
model fails on perfect Devanagari, no converter rescues the route; if it succeeds, a converter is
then worth building. Do not read those arms as "our transliterator works".

**Findings to carry forward:**
- **No commercially-safe open-source Urdu voice-cloning model exists** (as of 2026-08). The two that
  genuinely list Urdu *and* clone — Higgs Audio v3, OmniVoice — are both non-commercial. Every
  permissive cloner omits Urdu, or (Indic Parler-TTS) omits cloning; its maintainer says it always
  will. Full detail + licence classifications: `docs/URDU_MODEL_LICENSING.md`.
- **Weights licences ≠ code licences.** OmniVoice ships Apache-2.0 code with **CC-BY-NC weights**;
  the blogs calling it commercially free read the wrong file. Golden rule 6 was relaxed this session
  to permit NC weights **for personal use behind the API-key gate** — that is not a commercial licence.
- **⚠️ LoRA run 1's `lora_weights.safetensors` is GONE.** Only `lora_config.json` and
  `training_state.json` survive at `eval/results/voxcpm_lora/checkpoint_backup/`. Only run 2
  (`voxcpm_lora_proj`, `enable_proj:true`, 74 MB) still has weights, so **arm D uses run 2**.
  Retraining run 1 is ~15 min if it is ever wanted back.
- **Reference-speaker gap: CLOSED.** The owner supplied a consented female recording; it is
  `eval/fixtures/voice_urdu_female.wav` (`--reference-id female`) and every runnable arm ran against
  both speakers. **It is deliberately not committed** — a real person's voice, and the standing rule
  is no voice data in git without explicit sign-off. It exists on the pod and on local disk only, so
  a pod loss *and* a local loss would require re-recording. **Its transcript is still missing**, and
  IndicF5 needs one (`ref_text`); `_load_indicf5` raises rather than silently Whisper-ing the
  reference, because that would change what is being measured without saying so.
- **Every female cell scores a higher speaker cosine than its owner counterpart, in all five arms**
  (0.689–0.798 vs 0.662–0.737). A uniform offset across unrelated models points at ECAPA or the
  recordings, **not** at model quality. Do not read it as "these models clone women better."
- **The Perso-Arabic→Arabic-phonology hypothesis is NOT confirmed.** Arm B's owner cell has the
  worst CER in the table (0.189), which fits — but arm B's *female* cell is 0.0385, among the best.
  A model applying Arabic phonology to Arabic script would do it regardless of the target voice.
  Still `[INFER]`; only listening settles it.
- **Arm C (Devanagari) has the lowest CER at both references — treat that as a warning, not a win.**
  It is equally consistent with Devanagari producing Hindi-accented speech that Whisper transcribes
  *more* confidently, which is exactly the outcome the owner already rejected by ear.
- **Ladder rung B is untestable on this corpus:** `normalize_urdu` is a measured no-op on 13/13
  items because they were authored with clean Urdu codepoints. A null there means "input was already
  clean", not "normalization does not help".

Last updated: **2026-08-15** (see the checkpoint above — LoRA withdrawn, OmniVoice shipped, code-switch
bug fixed, live-verified end-to-end on a real pod that's now being terminated). Prior to that: Qwen
analyzer backend + frontend wiring are both merged to `main` and
done. The VoxCPM2 LoRA POC's training checkpoint merged earlier (PR #11); the baseline-vs-LoRA eval
comparison that was missing at that point has since run for real and **also merged to `main`**
(PR #12), with a **mixed result** — CER improved sharply, speaker-identity cosine regressed on Urdu
and only marginally improved on Hindi — and the owner has done a first, informal listen to the four
clips (found the LoRA Urdu clip good, the rest okay). Nothing has been shipped or flagged
`verified` off the back of this; see "What landed this session" below for the full picture.

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
| **VoxCPM2 LoRA POC** | 🚧 **Training (PR #11) and eval + human listen (PR #12) both merged to `main`.** Baseline-vs-LoRA comparison: CER improved sharply on Urdu (0.0818 → 0.0091) but speaker cosine regressed on Urdu (0.7226 → 0.6859, pass → fail) and only marginally improved on Hindi (0.6863 → 0.6986, still under gate) — a mixed result, not a clean win. The owner listened to all four clips informally and found the LoRA Urdu clip good, the rest okay — consistent with this project's established finding that the ECAPA speaker-cosine metric is out-of-distribution for this voice, not a contradiction of the numbers. **Not a rigorous (blind) listen, and no `LanguageSupport.verified` flag touched — merging the eval numbers is not a ship decision.** The actual `.safetensors` checkpoint weights are NOT in git (`.gitignore` excludes them project-wide) and exist only on the local Windows machine right now. The 36-clip training dataset (`eval/training/`) remains untracked/uncommitted, that consent decision is still open. Next step, still the owner's call: a rigorous blind listen, retrying with `enable_proj: true`, or deciding this is enough signal either way. Full detail: `docs/VOXCPM_LORA_POC.md`. |
| **Composer model picker** | ✅ Done, merged. Explicit model override + "(Recommended)" hint, no Tone control (confirmed no-op). |
| **Client-side audio extraction (Phase 3)** | ✅ Done, merged. |

**256 backend tests passing** as of the last full run this session (`feature/phase2-advanced-direction`
before its merge), ruff clean on everything touched. `gh` CLI is now installed locally (`winget install
GitHub.cli`), so PRs can be opened directly going forward instead of handed over as links.

## What landed this session (2026-08-12) — all three background agents resolved

All three pieces of parallel work from this session are now resolved — nothing is still running.

1. **Qwen2.5 LLM analyzer production backend — DONE, merged to `main` (commit `2be3759`).** Built
   `WireOp.CLASSIFY`, `QwenAnalyzerBackend` under `inference/runtimes/`, `AnalyzerScheduler` (a
   torch-free sibling to `InferenceScheduler`, not a `scheduler.py` edit), `JobKind.ANALYZE_LLM`,
   `POST /api/direction/analyze-llm`, an idle-unload timer. Pinned HF revision
   `aa8e72537993ba99e69dfaafa59ed015b17504d1`. Verified for real on a fresh pod: direct backend, full
   worker-subprocess path, and the real HTTP path all passed with 0 problems across en/ur/hi. Found and
   fixed a real bug (`load_time_sec` wasn't threaded from LOAD into the following classify call) during
   verification. 239 backend tests passing (reverified independently after merge), ruff clean.
   **Open risk, not resolved**: the idle-unload timer is the only VRAM mitigation between this
   scheduler and the audio one; they don't share a real budget.
2. **Qwen analyzer frontend wiring — DONE, merged to `main` (commit `1ae4e9f`).** An "AI suggest"
   button inside the existing Advanced per-segment editor calls the endpoint above, polls the job, and
   feeds the LLM's emotion/intensity/energy/rate classifications into the *same* `directionEdits` state
   a manual edit already uses (suggest-then-edit, not silent auto-apply) — segment text and
   `pause_after_ms` still come from the existing heuristic segment at that index, never from the LLM.
   The agent that built this stalled (a stream watchdog, not a real failure) right before committing —
   the work itself was complete and correct in its worktree; reviewed every diff by hand, verified the
   build, then committed/merged it manually. Frontend build green, backend untouched (239 still
   passing). Not click-tested against a live pod-backed backend (none was available) — TypeScript
   correctness and a mocked-response check were the extent of verification, noted explicitly as a gap.
3. **VoxCPM2 LoRA POC — training merged to `main` via PR #11; eval + human listen also merged via
   PR #12.** Training succeeded with real numbers (300 steps, ~15 min wall clock,
   ~2.3-2.4s/step steady-state, no OOM, `loss/stop` converged cleanly 0.039 → ~0.0001). The trained
   checkpoint was rescued off the pod before a scheduled shutdown — config/state committed at
   `eval/results/voxcpm_lora/checkpoint_backup/`, but the actual `.safetensors` weights are **not** in
   git (`.gitignore` excludes them project-wide, same as every other model's weights) and exist only
   on the local Windows machine right now. **The baseline-vs-LoRA eval comparison ran on a later pod
   (2026-08-13)**: CER improved sharply on the trained language (Urdu 0.0818 → 0.0091) but
   speaker-identity cosine — the actual thing this POC exists to move — regressed on Urdu
   (0.7226 → 0.6859, pass → fail) and only marginally improved on Hindi (0.6863 → 0.6986, still under
   gate). **The owner then listened to all four clips informally** and found the LoRA Urdu clip good,
   the rest okay — not a blind test, but consistent with this project's established finding
   (`docs/URDU_CLONING_REPORT.md`) that the ECAPA cosine metric is out-of-distribution for this voice,
   so a favorable human verdict on the one cell where cosine regressed is not a contradiction. **Still
   not shipped, no `LanguageSupport.verified` flag touched** — a rigorous blind listen is the
   recommended next step before any ship/no-ship call. Full detail: `docs/VOXCPM_LORA_POC.md`.

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

1. **Nothing is in flight.** Qwen analyzer backend + frontend wiring are merged to `main`. The LoRA
   POC's training (PR #11) and its real eval numbers + a first informal human listen (PR #12) are
   both merged to `main` — see the LoRA POC row above and `docs/VOXCPM_LORA_POC.md` for the full
   picture before deciding what's next.
2. Open decisions for the owner, not yet made: whether a rigorous blind listen is worth doing before
   any ship/no-ship call on LoRA, and whether to pursue another LoRA config (`enable_proj: true`) if
   the identity regression turns out to be real rather than a metric artifact. None of these should
   be decided unilaterally — see "What's left to build" above.
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
