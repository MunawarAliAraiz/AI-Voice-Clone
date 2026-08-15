# Roadmap

Durable copy of the plan approved 2026-08-09 for the "async jobs / mobile / perf" work. The
original plan lived only in a local Claude Code plan file (`~/.claude/plans/...`), which is not
readable by any other machine or agent — this file is the copy that survives. **Update the status
table at every checkpoint**, the same discipline [HANDOFF.md](HANDOFF.md) already asks for.

Read [CLAUDE.md](../CLAUDE.md) first — it has the non-negotiable invariants (golden rules) this
roadmap's work had to fit inside. This file is "what and why, in what order"; CLAUDE.md is "what
must never break while you do it."

---

## Where things stand

**Branch:** `feature/jobs-mobile-perf`, pushed to `fork` (`https://github.com/MunawarAliAraiz/AI-Voice-Clone`).
Not yet merged to `main`, no PR opened yet as of 2026-08-10.

| Phase | Status | Summary |
|---|---|---|
| **1 — Async jobs, Recent tab, mobile, perf** | ✅ Done | See below. `pytest -m "not gpu"` and `npm run build` both pass; end-to-end verified in-browser. |
| **2 — Speech Direction layer** | 🚧 Audibly wired, pod-unverified | IR + heuristic analyzer + capability report + `POST /api/direction/analyze` + UI chip + **multi-segment generation** are all in — direction now changes the actual audio, not just the preview. Not yet heard on real VoxCPM2 (needs the pod). LLM analyzer and Advanced editing still pending — see below. |
| **3 — Client-side audio extraction** | ✅ Built | Move video→audio extraction into the browser; server-side ffmpeg is now the fallback, not the only path. Covers both `EnrollCard` and the Audio Editor tab. |
| **4 — Chatterbox runtime + beyond** | 🔴 4a/4b/4c all done — **not verified, does not clone identity well enough to ship** | Real `ChatterboxBackend`, real Phase-A gate run, real human listen. Owner verdict: "not that good... identity is matched around 60%". Same conclusion shape as the Urdu investigation — a speaker-encoder ceiling, not a tunable parameter. Still unroutable in production, and not currently planned to be revisited without a LoRA fine-tune. See [PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md) and [PHASE_A_RESULTS.md](PHASE_A_RESULTS.md). |
| **Composer model picker** | ✅ Built | Backend already returned the full catalog (`GET /api/models`) and honored an explicit `model_id` override (`resolve()`'s `requested` param — honored or refused, never silently swapped); nothing in the frontend called either. Added `useModels()` + a "Model" select in `Composer.tsx`, filtered to specs that verifiably support the selected language, defaulting to Auto with the live-routed model shown as "(Recommended)" (reusing the existing `/api/detect-script` hint, no new endpoint). See below. |
| **VoxCPM2 LoRA POC** | ✅ Superseded by the Urdu bake-off below | Real LoRA fine-tune of VoxCPM2 on the owner's own voice (36 clips, 300 steps, ~15 min, no OOM), merged to `main` (PR #12). Its mixed CER-up/cosine-down result is exactly what the bake-off's blind listen (arm D) went on to resolve: the cosine regression did not survive to the ear. Full detail: [VOXCPM_LORA_POC.md](VOXCPM_LORA_POC.md). |
| **Urdu bake-off + LoRA integration** | 🔴 **Reversed** — LoRA withdrawn on real-app owner listening (2026-08-15) | 10-arm controlled bake-off (VoxCPM2 × 4 representations, OmniVoice, Higgs v3 attempted-and-blocked) on a fixed 13-item corpus × 2 reference speakers, blind-listened 130/130 by the owner. Arm D (VoxCPM2 + a personal LoRA) initially shipped as `voxcpm2_urdu_lora`, tied with arm C on the blind-listen score. **On real use through the running app, the owner judged base VoxCPM2 better than the LoRA** — contradicting the blind-listen median, which this project's "owner listening is authoritative" rule treats as final. The spec is deleted from the catalog (LoRA runtime plumbing kept, generic and free when unused); replaced by `voxcpm2_urdu_arabic` (same base checkpoint, no fine-tune) so Perso-Arabic Urdu isn't left with zero routes. `docs/URDU_BAKEOFF_RESULTS.md` §5a records the reversal. IndicF5 (arms H/I/J) remains blocked on an `HF_TOKEN`. Full methodology, all ten arms' numbers, and the blind-listen breakdown: [URDU_BAKEOFF_RESULTS.md](URDU_BAKEOFF_RESULTS.md). Licensing per model: [URDU_MODEL_LICENSING.md](URDU_MODEL_LICENSING.md). |
| **OmniVoice integration + code-switch routing fix** | ✅ Shipped, live-verified on a real pod (2026-08-15) | The bake-off's actual best-scoring arm (5.0/5 pronunciation, the only Urdu cell whose CER+cosine gate passes on both references) integrated as a fourth runtime: `RuntimeKind.OMNIVOICE`, `inference/runtimes/omnivoice.py`, CC-BY-NC (golden rule 6 amended to allow NC weights for personal use behind `VCS_API_KEY`, badged via `ModelSummary.commercial_use`). Real pod smoke test against the production `OmniVoiceBackend`: load 159.3s, synth 17.1s (peak 0.7573, non-silent), clean unload — found that OmniVoice lazily loads an embedded Whisper sub-model on the first `synth()` call when no `ref_text` is given. Alongside this, fixed a real production bug: code-switched Urdu (English loanwords inside Perso-Arabic — UrduSpeech is 57% code-switched) was rejected as `AmbiguousScriptError` before routing ran; `domain/language.py`'s `profile_text()` now treats Latin runs under 75% as loanword islands for languages with a native non-Latin script. Also fixed the model picker rendering maintainer notes as user-facing text (new `ModelSpec.caveat` field, ≤140 chars). **Live-verified end-to-end**: real pod backend tunneled to a local frontend (no ngrok — SSH tunnel + Vite's dev proxy), manual OmniVoice selection generated real audio through the actual job queue, and a fresh code-switched sentence (`...GitHub پر...pull request...`) correctly routed instead of 422ing. Confirmed Roman Urdu → OmniVoice is refused by design (`NoRouteError`, no `(ur, LATIN)` cell declared) — closing that gap was the transliteration probe below, which missed its own gate. `OMNIVOICE_URDU`'s cell stays `verified=False` pending a real gate re-run + fresh owner listen. Full detail: `docs/HANDOFF.md`'s in-flight section. |
| **Transliteration viability probe (Phase 2 of the Urdu plan)** | 🔴 **Ran, missed the gate (2026-08-15)** | `eval/run_translit_probe.py`, `Qwen/Qwen2.5-3B-Instruct` on the pod, scored Perso-Arabic→Devanagari and Roman→Devanagari against the corpus's hand-authored gold Devanagari. Mean CER 0.2771 / 0.3075, owner-sentence CER 0.15–0.48 — the plan's own gate ("close to gold or the transform layer isn't built") is not cleared, and the predicted Roman-easier-than-Perso-Arabic direction didn't hold either. **The `NoRouteError` on Roman Urdu → OmniVoice stays as-is** — no transform layer is being built on this model. Real errors seen went beyond vowel restoration (dropped words, semantic substitution, incomplete script-switching). Does not rule out a bigger model or a dedicated transliterator, just this one. Full numbers and qualitative examples: [URDU_BAKEOFF_RESULTS.md §8](URDU_BAKEOFF_RESULTS.md#8-phase-2--transliteration-viability-probe-bench). |

---

## Composer model picker (2026-08-11)

**Problem this solved:** the backend has always been able to list every model and honor an explicit
override (`domain/routing.py::resolve`'s `requested` param, `GET /api/models`) — but no UI ever called
either. Every generation was fully automatic and invisible until *after* the job was queued (the
route chip on the result card).

**What shipped:** `frontend/src/hooks/queries.ts` gained `useModels()`. `Composer.tsx` gained a
"Model" field between Language and Speed: options are every catalog spec that verifiably supports the
selected language (`m.languages.some(l => l.language === language)` — a spec with no verified cell
for this language is never offered, so the picker can't produce a request `resolve()` would 422), plus
an "Auto" option that stays selected by default. The model `/api/detect-script` would actually route
to (already computed for the existing live hint) is labeled "(Recommended)" on both the Auto option
and its matching explicit entry, so the recommendation is visible whether or not the user overrides
it. Picking a model no longer supported after a language change resets to Auto rather than sending a
stale id. The bottom detect-hint line was updated to reflect the *effective* model (override or auto),
not always the auto pick, so it can't look inconsistent with what Generate will actually do.

**Deliberately not built alongside this:** a Tone selector. Tone is never analyzer-derived (every
segment defaults to `Tone.NEUTRAL`) and no current runtime honors it — VoxCPM has no tone knob, and
Chatterbox (the only model with any relevant params) was just concluded not accurate enough to ship
(see Phase 4c below). A selectable control with a confirmed-zero audible effect is the exact
no-op-slider pattern Phase 1 Part C deleted Emotion/Style Exaggeration for — skipped on the owner's
explicit call, not an oversight.

## Phase 1 — Done (2026-08-09)

### Context: what this replaced

An audit of two fork branches (`fork/main`, `origin/bugfix/fix-bugs`, both ahead of `main`) found
Speed/Stability/Audio-Editor/video-ingest were real, but two features were not:

- **Emotion** — post-hoc DSP, not model conditioning, and *broken*: the pitch math hardcoded 24 kHz
  while VoxCPM emits 48 kHz, so 4 of 7 presets played slowed and pitch-dropped.
- **Style Exaggeration** — injected into synth params that **no runtime reads**. Pure dead code.

Both were deleted rather than patched — see Part C below. Neither is coming back until Chatterbox
(Phase 4) gives the product a real `exaggeration` knob to attach a control to.

### Part A — Async job queue

`POST /api/generate` now enqueues a row and returns **202** immediately instead of blocking the
request on synthesis. This also fixes a real bug: the old code wrapped the worker call in
`asyncio.shield` so a disconnected client's synthesis still completed and wrote a file, but
`db.create_generation()` never ran — an orphaned file with no history row. Writing the job row
*before* synthesis starts closes that hole.

**The architectural rule that matters most:** the `jobs` table **is** the queue.
`backend/app/inference/scheduler.py` was not touched — no FIFO object went into it, so the
eviction-under-slot invariant (golden rule 3) and all five scheduler tests stayed green with zero
edits. A `JobRunner` (`backend/app/jobs/runner.py`) claims rows with an atomic
`UPDATE ... RETURNING`, one worker pool per job `kind` (currently just `SYNTHESIZE`, concurrency 1 —
matches the GPU slot). Route is resolved **once, at enqueue**, and stored on the row; the handler
never calls `resolve()` again at claim time (golden rule 4 — routing must never consult live state).

Key files: `backend/app/jobs/{types,estimate,runner}.py`,
`backend/app/jobs/handlers/synthesize.py`, `backend/app/api/routers/jobs.py`
(`GET /api/jobs`, `GET /api/jobs/{id}`, `DELETE /api/jobs/{id}`), `backend/app/db/schema.sql`'s
`jobs` table. Reaping (a `running` row found at startup is dead by definition — single uvicorn
worker), queued-job expiry, and terminal-job retention all run once at `main.py` startup, before the
runner starts accepting new work. No automatic retries — a silent retry of a 21-minute generation is
the same species of bug as a silent fallback.

Tests: `backend/tests/test_jobs_{runner,recovery,estimate}.py`, `test_api_jobs.py`. 34 new tests.

### Part B — Frontend: TanStack Query + Recent tab

Adopted `@tanstack/react-query` v5 — this makes true a claim `CLAUDE.md` already made before any of
this code existed (`frontend/src/hooks/queries.ts`). Job polling is `useJob(id)` with a
function-form `refetchInterval` that returns `1500` while non-terminal and `false` once the job
settles — no manual `setInterval`, no cleanup bugs.

New **Recent** tab (`frontend/src/components/JobsPanel.tsx`) shows every job — including ones still
queued, running, or failed before a `generation_history` row ever existed, which `HistoryPanel`
structurally cannot show. Toasts (`frontend/src/components/Toast.tsx`, no external library) fire
once per job the moment it first reaches a terminal status.

Golden-rule risk that had to be actively guarded: a fake-runtime job's completed response must carry
**both** `X-Fake-Audio: true` (header) and `is_fake: true` (body field) — a header alone doesn't
survive if a proxy strips it (the Cloudflare Worker proxies `/api/*` in production). Both are set in
`build_job_status_response()` in `backend/app/api/routers/jobs.py`.

### Part C — Removed Emotion and Style Exaggeration

Deleted `EMOTION_FILTERS` and the exaggeration branches from `backend/app/audio.py`
(`apply_audio_effects` now does `speed` only, real ffmpeg `atempo`); dropped both fields from
`schemas/tts.py` and `Composer.tsx`; deleted `test_emotion.py` and `test_style_exaggeration.py`.

### Part D — Mobile responsiveness (375px / 320px, verified in-browser)

Real bugs found and fixed, not just breakpoint tuning:

- `input[type=range]` and `input[type=checkbox]` were inheriting the global text-input rule
  (`width:100%; padding:10px; border`) — every slider in the app rendered as a bordered box, not a
  thin track. Reset in `App.css`.
- A flex `shrink-to-fit` quirk (a plain `<div>` centered inside a `flex-direction: column` container
  with `align-items: center`, no `align-self: stretch`) meant the Audio Editor's empty-state hint
  text never wrapped and overflowed the viewport at 320px. Fixed by giving that wrapper
  `width: 100%`.
- `.file-drop span { white-space: nowrap; ... }` was written for `EnrollCard`'s one-line truncated
  filename, but the class was reused by `AudioEditorTab`'s multi-line hint text, which it then
  silently truncated. Scoped the truncation rule to a new `.file-drop-label` class instead of the
  bare-tag selector.
- Topbar wraps below ~560px instead of overflowing; tabs collapse to icon-only (with `aria-label`
  preserving the accessible name — the label text is still in the DOM, just visually hidden, not
  removed from the a11y tree).
- Added the CSS-var CI check `CLAUDE.md` already claimed existed (`frontend/scripts/check-css-vars.mjs`,
  wired into `npm run build` via `check:css-vars`). Pruned 7 genuinely-unused design tokens; fixed
  `--fg` (used, never declared — silently fell back to a hardcoded hex).

### Part E — LCP / FCP / TBT

- Favicon: 1024×1024 PNG at 466 KB → 64×64 at 5.3 KB.
- Google Fonts: 6 weights requested (300–800), only 400/500/600 ever used anywhere in the codebase
  (grep-verified) → down to 3, loaded non-blocking (`media="print"` swap trick) instead of
  render-blocking.
- `encodeWav` (two full passes over every audio sample — ~1.4M iterations for a 15s clip at 48kHz)
  moved off the main thread into a Web Worker (`frontend/src/workers/wavEncoder.worker.ts`), so it no
  longer blocks the UI the instant the user hits Stop while recording.
- The recording level meter (`EnrollCard.tsx`) used to call `setState` on every
  `requestAnimationFrame` (~60/s), re-rendering the whole component tree. It now writes a `--level`
  CSS custom property directly to the DOM via a ref; each meter bar's `transform` is pure CSS
  (`calc(var(--level) * var(--phase) * 2.2)`), so a recording session causes **zero** React
  re-renders for the animation.
- `new File()` in `EnrollCard` was being reconstructed on every render (e.g. every keystroke in the
  name field), giving `AudioEditor`'s `file` prop a new identity each time and re-firing its
  `useEffect` (fresh object URL, fresh `<audio>` metadata fetch) for audio that hadn't changed.
  `useMemo`'d.
- `AudioEditorTab` (the biggest single component, 477 lines) is now `React.lazy`-loaded — it no
  longer ships in the bundle a session that never opens that tab pays for. Main bundle 255 KB → 245
  KB + an 11 KB chunk loaded on demand.
- Removed the `react-router-dom` dependency — it was never imported anywhere.
- `frontend/src-tauri/` removed from git and gitignored — stray generated Tauri schema output with
  no `Cargo.toml`, `tauri.conf.json`, or `@tauri-apps/*` dependency anywhere in the repo to back it
  (the desktop shell was dropped project-wide during the rewrite; see `docs/REWRITE_PLAN.md`).

### Verification run

```
cd backend && uv run pytest -m "not gpu"   # passes
cd frontend && npm run build               # passes (tsc -b && check:css-vars && vite build)
```

Manual, in-browser: generate → 202 → poll → Recent tab shows the job with correct status chip/route
chip/error detail; 375px and 320px screenshots on Studio, Recent, and Audio Editor tabs show zero
horizontal scroll (`document.documentElement.scrollWidth === clientWidth` checked via JS at each
width); microphone-denied path in `useRecorder` still degrades gracefully after the refactor to
callback-based level reporting.

---

## Phase 2 — Speech Direction layer (preview landed)

### Landed (2026-08-09)

The **read-only preview** is built, tested, and wired end-to-end:

- **IR** — `backend/app/domain/direction.py`: the frozen, pure `DirectionPlan` (segments carrying
  emotion / tone / intensity / energy / rate / emphasis / `pause_after_ms`, plus a `DirectionSummary`
  for simple mode). Stdlib-only, no I/O, no inference imports.
- **Heuristic analyzer** — `backend/app/domain/direction_analyze.py`: real per-language keyword
  lexicon (English, Roman + Perso-Arabic Urdu, Roman + Devanagari Hindi) + punctuation/caps/intensifier
  signals → `DirectionPlan`. Pure, deterministic, offline. `analyze()` signature is frozen so the LLM
  analyzer drops in behind it. 32 tests in `test_direction_analyze.py`.
- **Capability report + renderer** — `backend/app/jobs/direction.py`: `capability_for(spec)` declares,
  per IR field, HONORED / APPROXIMATED / IGNORED (VoxCPM: honors segmentation/pause/rate, approximates
  intensity→`cfg_value` and emphasis, **ignores emotion/tone/energy — visibly**). `render(plan, spec)`
  maps the plan to per-segment knobs, and IGNORED fields provably never leak into params. 8 tests in
  `test_direction_capability.py`, incl. the `DIRECTION_FIELDS`↔IR sync contract.
- **Endpoint** — `POST /api/direction/analyze` (`routers/direction.py` + `schemas/direction.py`):
  routes exactly like `/generate` (pure `resolve()`), returns the plan + capability report + the same
  `RouteInfo` chip. Read-only: generates no audio, enqueues no job.
- **UI** — `frontend/src/components/DirectionPanel.tsx`: the capability chip (honored→approximated→
  ignored, rationale tooltips) + summary line + expandable per-segment detail, debounced into the
  Composer behind a "Direction (preview)" disclosure. RTL keys off `source_script`, never `language`.
- **Multi-segment generation (2026-08-10)** — direction now audibly affects output, not just the
  preview. `POST /generate` gained `apply_direction: bool` (default false, unchanged behavior). When
  true, the router calls `analyze()` + `render()` at enqueue (same pure-at-enqueue discipline as
  routing — rule 4) and stores per-segment `{text, params, speed, pause_after_ms}` on the job.
  `jobs/handlers/synthesize.py` branches on whether `segments` is present: directed jobs call
  `scheduler.synthesize()` once **per segment** (own text/cfg_value/tempo), then
  `audio.concat_wavs_with_pauses()` joins the real per-segment WAVs with real inter-segment silence —
  every sample is either real model output or explicit silence, nothing fabricated (rule 1). Temp
  segment files are always cleaned up (`finally`); the final path is recorded on the job row before
  synthesis starts, same orphan rule as single-shot. `TTSGenerateResponse.segment_count` (1 for
  normal, >1 when directed) surfaces in the Composer's result chip and the Recent tab, so a directed
  clip visibly says so. The Composer only shows the "Apply this direction" checkbox *inside* the
  capability-chip disclosure — the user must see what will/won't take effect before opting in.
  33 new backend tests (concat primitive, the ffmpeg-missing-on-PATH graceful-degrade this surfaced as
  a pre-existing gap and fixed the same way `routers/media.py`'s conversion path already was, the
  directed job handler via a WAV-writing scheduler double, and the HTTP surface). 245 backend tests
  total, ruff-clean, frontend build green. **Not yet validated with real audio — needs the pod** (this
  box has no GPU/VoxCPM worker configured; local runs correctly reach the real scheduler and fail only
  on `no interpreter configured for runtime 'voxcpm'`, the same failure single-shot generation already
  hits here).

### Remaining

1. **Real-audio pod validation — objective checks passed (2026-08-12), human listen still open.**
   Hit the real `POST /api/generate` with `apply_direction: true` on the pod against a live VoxCPM2
   worker (3-sentence Hindi text mixing neutral/exclamatory/question segments, enrolled profile from
   `eval/fixtures/voice_urdu.wav`), polled the job to `succeeded`, downloaded the resulting WAV
   (`segment_count: 3`, `duration_sec: 4.252`, real 48kHz output — not resampled, matching VoxCPM2's
   own rate as documented). Automated analysis of the actual samples: no NaN, no silence-only regions,
   real RMS energy throughout, **zero sample-to-sample discontinuities above a 0.5 abs-diff click
   threshold anywhere in the file**, and exactly 2 silence runs (~0.25s each) at the 2 expected
   inter-segment boundaries for a 3-segment clip — `concat_wavs_with_pauses` is splicing where the
   directed plan says it should, with no audible-discontinuity signature. **Not yet confirmed
   perceptually** (does the emphasis/cfg_value difference actually sound like a delivery change,
   independent of what's measurable in the raw samples) — the clip is saved at
   `eval/results/direction/pod_directed_hi.wav`, awaiting a human listen before this line says
   "verified" rather than "objectively sound."
2. **LLM analyzer (Qwen2.5-Instruct, Apache-2.0)** — the flagged second implementation behind the frozen
   `analyze()` signature. **Pod-only** (GPU, model download, runs in a worker to keep torch out of the
   API; validate on the pod, not the GPU-less Windows box). Cached per `(text, language)`, run as its own
   job `kind`. This is the "analyzer" half of the owner's "both, analyzer first" decision.
   **Capability probe passed (2026-08-12):** `eval/run_qwen_analyzer_probe.py` against
   `Qwen/Qwen2.5-3B-Instruct` (unpinned, probe-only) on the pod — 0 validation problems across 6 cases
   spanning English/Roman-Urdu/Hindi and neutral/happy/sad/anxious/excited/angry, structured-JSON output,
   2.1-3.7s generation per case, 19-27s cold load. Segmentation/emphasis/pause_after_ms stay on the
   existing heuristic; the LLM classifies only emotion/intensity/energy/rate for already-segmented
   sentences. Production build (`QwenAnalyzerBackend`, dedicated worker/protocol, `JobKind.ANALYZE_LLM`,
   `POST /api/direction/analyze-llm`) **landed on `main` (2026-08-12)**, backend only. Real pod
   verification (fresh RunPod instance): direct backend load→classify→unload (0 problems, en/ur/hi),
   the full worker-subprocess + `AnalyzerScheduler` path, and the real HTTP path
   (`POST /api/direction/analyze-llm` → 202 → polled `succeeded`, correct rows, one worker reused
   across requests — `load_time_sec` nonzero only on the cold call). A real bug was found and fixed
   during verification: `load_time_sec` wasn't threaded from the LOAD response into the following
   classify call. 239 backend tests passing, ruff clean on all touched files. **Frontend wiring not
   built yet** — the endpoint exists, nothing in the UI calls it. **Open risk, documented not
   silently accepted**: the idle-unload timer is the only VRAM-contention mitigation between this
   scheduler and the audio `InferenceScheduler` — they don't share a real budget, so a resident Qwen
   worker (~6GB) alongside a resident audio model on the 20GB pod card is a real risk during
   overlapping requests, not just at idle.
3. **Advanced editing** — the panel is read-only today; the full per-segment IR editor (the "Advanced
   tab for pros") is not built. Simple mode (summary + chip + apply checkbox) is what shipped.
   **Backend contract landed (2026-08-11):** `direction_plan` on `TTSGenerateRequest` accepts
   client-edited per-segment prosody overrides, sparse/index-keyed, re-validated server-side against a
   fresh `analyze()` of the same text (422 on a stale/unknown index) — never a client-controlled segment
   text or emphasis. Frontend editor UI in progress.

**Original design notes (unchanged):**

**Problem this solves:** today "Emotion" was a fake per-request DSP filter. A real one needs to (a)
work per *segment* of the text, not the whole request, and (b) be honest about what each model can
actually do with it — VoxCPM takes `cfg_value` + `inference_timesteps` and no instruction text at
all; Chatterbox declares a real `exaggeration` parameter but has no runtime yet (Phase 4).

**Design:**

1. Text → a structured IR: segments, each with emotion, intensity, tone, energy, rate, emphasis,
   inter-segment pauses.
2. A renderer **per model** that declares which IR fields it *honors*, *approximates*, or *ignores* —
   surfaced in the UI as a visible chip, exactly like the existing `route: {lossy, rationale}` chip.
   This is what keeps this feature inside golden rule 5 (no silent fallback) instead of becoming
   another slider that silently does nothing, which is exactly the bug Style Exaggeration was.
3. On VoxCPM specifically, the honored set is: segmentation, inter-segment pauses, per-segment speed,
   per-segment `cfg_value`, and emphasis via text/punctuation — everything else is declared
   "ignored", visibly.
4. UX: simple mode auto-detects and surfaces ~3 controls; an Advanced tab exposes the full IR. (This
   is also where the earlier "simple by default, Advanced tab for pros" UX request lives — it wasn't
   only about Speech Direction, but Speech Direction is the first feature that actually needs it.)
5. Analyzer: heuristics first (punctuation, sentence splitting, a per-language keyword lexicon — zero
   dependencies, works offline, including on the pod which has no internet-dependent LLM access by
   default). A Claude-backed analyzer is a second implementation behind a settings flag, cached per
   `(text, language)`, and run as its own job `kind` (the `jobs` table already supports this — see
   Phase 1 Part A) so its latency never blocks the UI thread or the synth queue.

**Why this is Phase 2 and not Phase 1:** building the IR before the renderer's honored/ignored
declaration exists would just be Style Exaggeration again with extra steps. The capability-map design
above is the part that makes it not a repeat of that mistake — build the declaration mechanism first.

## Phase 3 — Client-side audio extraction (built)

**Problem this solved:** uploading a full video file just to enroll a 6–15s reference clip was
expensive in bandwidth and server time, and gave the user no way to pick *which* part of a longer
recording to use.

**What shipped:** [`frontend/src/lib/wavEncode.ts`](../frontend/src/lib/wavEncode.ts) factors the
`encodeWavOffMainThread` primitive out of `useRecorder.ts` so both the recorder and the new
extraction path share one Web-Worker encoder.
[`frontend/src/lib/clientAudioExtract.ts`](../frontend/src/lib/clientAudioExtract.ts) adds
`decodeMediaFile()` (`AudioContext.decodeAudioData`) and `extractWavClip()`, capped at
`MAX_CLIENT_CLIP_SEC = 30`. `EnrollCard.tsx` decodes the picked file on selection: files ≤30s are
extracted automatically; files >30s show
[`ClipRangeSelector.tsx`](../frontend/src/components/ClipRangeSelector.tsx) — a single drag handle
that positions a fixed 30s window over the source, defaulting to the first 30s — and the selected
window is what gets extracted and uploaded, never the full file.

**The caveat this design flagged did ship, not just the code:** `decodeAudioData` handles mp4/webm
audio tracks in most browsers but **fails on mkv/avi/flv**, which `EnrollCard`'s `accept` list still
allows. When decode throws, `EnrollCard` shows a visible "couldn't preview this file's audio in your
browser — it will be uploaded as-is and processed on the server instead" message and falls back to
uploading the original file whole, letting the existing server-side ffmpeg path in
`backend/app/audio.py::_transcode_to_wav` handle it — an explicit, visible fallback, not a silent
one, per golden rule 5. Verified in-browser for all three paths (≤30s auto-extract, >30s drag-select
with correct clamping, and the undecodable-format fallback message).

**Audio Editor tab (`AudioEditorTab.tsx`) now covered too:** it accepts video the same way, decodes
on file pick, and re-extracts (debounced) whenever the drag-selector's window changes — the working
`file` becomes the extracted clip, and the existing trim/speed/pitch/gain/fade controls now operate
on that clip instead of the full original upload. The original file's name/size are kept separately
(`originalName`/`originalSizeMb`) purely for display, since the extracted `clip.wav` has neither. The
undecodable-format fallback message and behavior mirror `EnrollCard` exactly. Verified in-browser for
all three paths there too (≤30s auto-extract, >30s drag-select with correct re-extraction on drag,
and the undecodable-format fallback).

## Phase 4 — Chatterbox runtime + IR taxonomy expansion

**Problem this solves:** Speech Direction's `emotion`/`tone` fields are real in the IR but currently
IGNORED by every runtime — VoxCPM takes no emotion/tone conditioning at all, so today "angry" has
zero audible effect. Chatterbox (`RuntimeKind.CHATTERBOX`, already a researched stub in
`catalog.py`) is the model that could change that: MIT-licensed, real `exaggeration`/`cfg_weight`
params confirmed against the HF card/GitHub/PyPI. But it exposes exactly **two** continuous knobs
while the IR has **four** fields that plausibly want to drive them (`emotion`, `tone`, `intensity`,
`energy`) — deciding how those collapse onto two knobs, without one field silently overwriting
another, was the actual design problem. Full write-up, including the blend table, the
`ChatterboxBackend` shape (mirroring `voxcpm.py`), the `language_id` plumbing gap, VRAM/licensing/
routing risk, and the sequencing plan below:
**[docs/PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md)**.

**Landed 2026-08-10, ahead of the runtime work:** the IR taxonomy gained `Emotion.ANXIOUS`
(detected by the heuristic analyzer today, same as `ANGRY`) and `Tone.NARRATIVE` (narrator/
commentary delivery style — not yet analyzer-derived, deliberately deferred rather than shipping a
fabricated-looking heuristic; see `direction_analyze.py`'s docstring). Both are inert until a
renderer honors them — same honest-until-proven-otherwise discipline as the rest of Phase 2.

**Phase 4a — done (CPU-only, no GPU):** `app/jobs/direction.py` gained `_CHATTERBOX_FIELDS` (the
per-field HONORED/APPROXIMATED/IGNORED table) and the `(intensity, energy, emotion-arousal) →
exaggeration`, `rate → cfg_weight` blend functions from the design doc's §5, both clamped to the
catalog spec's declared ranges. `render()` also injects `params["language_id"] = plan.language` for
Chatterbox specifically, closing the `SynthRequest`-has-no-`language`-field gap the design doc
flagged — pass-through, **confirmed correct by Phase 4b's introspection**: Chatterbox's
`SUPPORTED_LANGUAGES` contains `'en'`/`'hi'` matching this project's `LanguageCode` values exactly.
`tone` stays IGNORED, matching the design doc's explicit decision (nothing populates it, and the two
knobs are already spoken for). `test_direction_capability.py`'s `_NON_VOXCPM` fixture was repointed
at an F5 spec (was Chatterbox, which now has its own real mapping and could no longer serve as "the
generic example"). 6 new tests added, 223 total, all CPU-only; `ruff check` clean. **This is still
inert in production** — Chatterbox's `LanguageSupport` cells aren't `verified=True`, so
`domain.routing.resolve()` cannot route to it yet (by design, the same gate VoxCPM had to clear).

**Phase 4b — done (2026-08-11), real GPU pod:** `app/inference/runtimes/chatterbox.py`
(`ChatterboxBackend`) landed, `make_backend()`'s dispatch branch wired, the `pyproject.toml`
`chatterbox` extra filled in (`chatterbox-tts>=0.1.7`), and `pod-bootstrap.sh` now provisions a
`.venv-chatterbox` alongside VoxCPM's. Real smoke test on the pod: load (35.2s), English synth (6.36s
audio / 7.2s wall, RTF≈1.1), Hindi synth reusing the same loaded checkpoint, 24kHz output as declared,
`unload()` verified to drop GPU memory back to ~0. Uncovered and fixed a real trap along the way:
`chatterbox-tts`'s watermarking dependency silently no-ops under `setuptools>=81` (which removed
`pkg_resources`) — pinned `setuptools<81` in the venv, documented in `CLAUDE.md`'s traps list. Also
corrected two wrong API assumptions from the original design doc — see
[PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md) §3. Still not routable in production; no
`pytest -m gpu` file exists for this yet (see the design doc's §11 for why, and what would be needed).

**Phase 4c — done (2026-08-11), concluded NOT VERIFIED:** real Phase-A gate run on the pod
(`eval/run_chatterbox_synth.py` + `eval/eval_harness.py`) against the same reference speaker and
target sentence used for F5/VoxCPM2. Hindi passed all three numeric gates on the first sample (CER
0.0526, speaker cosine 0.7271, RTF 0.76); English missed the speaker gate at 0.6848 (borderline, n=1).
Per the owner's listen — "not that good... identity is matched around 60%" — **neither cell is
`verified=True`.** Same conclusion shape as the Urdu investigation below: a numeric pass is a screen,
not a verdict, and a borderline-passing speaker-cosine score correctly predicted a borderline,
unconvincing identity match by ear. Full writeup: [PHASE_A_RESULTS.md](PHASE_A_RESULTS.md). Not
currently planned to be revisited without a LoRA fine-tune (see the Urdu report's same conclusion).
Also fixed a real TorchCodec/CUDA-runtime-mismatch trap hit while building `.venv-eval`, now
documented in `CLAUDE.md` and scripted into `pod-bootstrap.sh`.

- Multi-speaker generation.
- Dubbing pipeline.
- Post-processing presets (building on the non-destructive edit primitives the Audio Editor already
  has: trim, speed, pitch, gain, fade, LUFS normalize, silence removal).

These three have no design write-up yet — start with a plan, the way Phase 1, 2, and Chatterbox did,
before writing code.
