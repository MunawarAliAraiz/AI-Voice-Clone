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
| **3 — Client-side audio extraction** | 📋 Designed, not started | Move video→audio extraction into the browser; server-side ffmpeg becomes the fallback, not the only path. |
| **4 — Chatterbox runtime + beyond** | 📋 Designed, not started | Real `exaggeration`/`cfg_weight` knobs, multi-speaker, dubbing, post-processing presets. See [PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md). |

---

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

1. **Real-audio pod validation** — confirm actual VoxCPM2 output through the directed path: segment
   boundaries land where expected, joined audio has no clicks/discontinuities at the splice points,
   and per-segment `cfg_value` audibly changes delivery. Nothing here should differ from single-shot
   VoxCPM2 behavior (same worker, same wire protocol, called N times instead of once), but it is
   unverified until heard.
2. **LLM analyzer (Qwen2.5-Instruct, Apache-2.0)** — the flagged second implementation behind the frozen
   `analyze()` signature. **Pod-only** (GPU, model download, runs in a worker to keep torch out of the
   API; validate on the pod, not the GPU-less Windows box). Cached per `(text, language)`, run as its own
   job `kind`. This is the "analyzer" half of the owner's "both, analyzer first" decision.
3. **Advanced editing** — the panel is read-only today; the full per-segment IR editor (the "Advanced
   tab for pros") is not built. Simple mode (summary + chip + apply checkbox) is what shipped.

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

## Phase 3 — Client-side audio extraction (designed, not built)

**Problem this solves:** the Audio Editor tab currently uploads full video files to the backend to
extract audio server-side via ffmpeg — expensive in bandwidth and server time for what is usually a
30-second clip.

**Design:** decode in-browser via `AudioContext.decodeAudioData`, enforce a client-side ≤30s clip
limit, send only the extracted WAV to the backend.
[`useRecorder.ts`](../frontend/src/hooks/useRecorder.ts) already contains the exact
`decodeAudioData` → `encodeWav` primitive this needs (and Phase 1 Part E already moved that encode
step into a Web Worker, which this reuses directly).

**Caveat that must ship in the UI, not just the code:** `decodeAudioData` handles mp4/webm audio
tracks in most browsers but **fails on mkv/avi/flv**, which `EnrollCard`'s current `accept` list
allows. Keep the server-side ffmpeg path as an **explicit, visible fallback** for those formats —
not a silent one. A silent fallback here is the same class of bug golden rule 5 exists to prevent,
just on the upload path instead of the generation path.

Also: relabel the Audio Editor tab to say it extracts audio *from video*, and prompt for a
30-second clip at the file picker — the tab's current name is misleading about what it does.

## Phase 4 — Chatterbox runtime + IR taxonomy expansion (designed, not built)

**Problem this solves:** Speech Direction's `emotion`/`tone` fields are real in the IR but currently
IGNORED by every runtime — VoxCPM takes no emotion/tone conditioning at all, so today "angry" has
zero audible effect. Chatterbox (`RuntimeKind.CHATTERBOX`, already a researched stub in
`catalog.py`) is the model that could change that: MIT-licensed, real `exaggeration`/`cfg_weight`
params confirmed against the HF card/GitHub/PyPI. But it exposes exactly **two** continuous knobs
while the IR has **four** fields that plausibly want to drive them (`emotion`, `tone`, `intensity`,
`energy`) — deciding how those collapse onto two knobs, without one field silently overwriting
another, is the actual design problem. Full write-up, including the proposed blend table, the
`ChatterboxBackend` shape (mirroring `voxcpm.py`), the `language_id` plumbing gap, VRAM/licensing/
routing risk, and a sequencing plan (4a: CPU-only mapping + tests → 4b: real backend, GPU → 4c:
Phase-A validation): **[docs/PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md)**.

**Landed now, ahead of the runtime work (2026-08-10):** the IR taxonomy gained `Emotion.ANXIOUS`
(detected by the heuristic analyzer today, same as `ANGRY`) and `Tone.NARRATIVE` (narrator/
commentary delivery style — not yet analyzer-derived, deliberately deferred rather than shipping a
fabricated-looking heuristic; see `direction_analyze.py`'s docstring). Both are inert until a
renderer honors them — same honest-until-proven-otherwise discipline as the rest of Phase 2.

- Multi-speaker generation.
- Dubbing pipeline.
- Post-processing presets (building on the non-destructive edit primitives the Audio Editor already
  has: trim, speed, pitch, gain, fade, LUFS normalize, silence removal).

These three have no design write-up yet — start with a plan, the way Phase 1, 2, and Chatterbox did,
before writing code.
