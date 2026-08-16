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

**Branch:** everything below is merged to **`main`** (`fork` =
`https://github.com/MunawarAliAraiz/AI-Voice-Clone`, the push target — not `origin`). The
`feature/jobs-mobile-perf` branch this file was first written against is long merged; work since
then has gone through one short-lived feature branch + PR per change, none outstanding as of
2026-08-15.

| Phase | Status | Summary |
|---|---|---|
| **1 — Async jobs, Recent tab, mobile, perf** | ✅ Done | See below. `pytest -m "not gpu"` and `npm run build` both pass; end-to-end verified in-browser. |
| **2 — Speech Direction layer** | 🚧 Built end-to-end (heuristic + LLM analyzer + Advanced editor), one human-listen item open | IR + heuristic analyzer + LLM analyzer (frontend included, 2026-08-13) + capability report + `POST /api/direction/analyze` + UI chip + Advanced per-segment editor (2026-08-12) + **multi-segment generation** are all in and on `main`. Only remaining item: the multi-segment audio clip passed every objective check on the pod but was never perceptually confirmed — see below. |
| **3 — Client-side audio extraction** | ✅ Built | Move video→audio extraction into the browser; server-side ffmpeg is now the fallback, not the only path. Covers both `EnrollCard` and the Audio Editor tab. |
| **4 — Chatterbox runtime + beyond** | 🔴 4a/4b/4c all done — **not verified, does not clone identity well enough to ship** | Real `ChatterboxBackend`, real Phase-A gate run, real human listen. Owner verdict: "not that good... identity is matched around 60%". Same conclusion shape as the Urdu investigation — a speaker-encoder ceiling, not a tunable parameter. Still unroutable in production, and not currently planned to be revisited without a LoRA fine-tune. See [PHASE4_CHATTERBOX_DESIGN.md](PHASE4_CHATTERBOX_DESIGN.md) and [PHASE_A_RESULTS.md](PHASE_A_RESULTS.md). |
| **Composer model picker** | ✅ Built | Backend already returned the full catalog (`GET /api/models`) and honored an explicit `model_id` override (`resolve()`'s `requested` param — honored or refused, never silently swapped); nothing in the frontend called either. Added `useModels()` + a "Model" select in `Composer.tsx`, filtered to specs that verifiably support the selected language, defaulting to Auto with the live-routed model shown as "(Recommended)" (reusing the existing `/api/detect-script` hint, no new endpoint). See below. |
| **VoxCPM2 LoRA POC** | ✅ Superseded by the Urdu bake-off below | Real LoRA fine-tune of VoxCPM2 on the owner's own voice (36 clips, 300 steps, ~15 min, no OOM), merged to `main` (PR #12). Its mixed CER-up/cosine-down result is exactly what the bake-off's blind listen (arm D) went on to resolve: the cosine regression did not survive to the ear. Full detail: [VOXCPM_LORA_POC.md](VOXCPM_LORA_POC.md). |
| **Urdu bake-off + LoRA integration** | 🔴 **Reversed** — LoRA withdrawn on real-app owner listening (2026-08-15) | 10-arm controlled bake-off (VoxCPM2 × 4 representations, OmniVoice, Higgs v3 attempted-and-blocked) on a fixed 13-item corpus × 2 reference speakers, blind-listened 130/130 by the owner. Arm D (VoxCPM2 + a personal LoRA) initially shipped as `voxcpm2_urdu_lora`, tied with arm C on the blind-listen score. **On real use through the running app, the owner judged base VoxCPM2 better than the LoRA** — contradicting the blind-listen median, which this project's "owner listening is authoritative" rule treats as final. The spec is deleted from the catalog (LoRA runtime plumbing kept, generic and free when unused); replaced by `voxcpm2_urdu_arabic` (same base checkpoint, no fine-tune) so Perso-Arabic Urdu isn't left with zero routes. `docs/URDU_BAKEOFF_RESULTS.md` §5a records the reversal. IndicF5 (arms H/I/J) remains blocked on an `HF_TOKEN`. Full methodology, all ten arms' numbers, and the blind-listen breakdown: [URDU_BAKEOFF_RESULTS.md](URDU_BAKEOFF_RESULTS.md). Licensing per model: [URDU_MODEL_LICENSING.md](URDU_MODEL_LICENSING.md). |
| **OmniVoice integration + code-switch routing fix** | ✅ Shipped, live-verified on a real pod (2026-08-15) | The bake-off's actual best-scoring arm (5.0/5 pronunciation, the only Urdu cell whose CER+cosine gate passes on both references) integrated as a fourth runtime: `RuntimeKind.OMNIVOICE`, `inference/runtimes/omnivoice.py`, CC-BY-NC (golden rule 6 amended to allow NC weights for personal use behind `VCS_API_KEY`, badged via `ModelSummary.commercial_use`). Real pod smoke test against the production `OmniVoiceBackend`: load 159.3s, synth 17.1s (peak 0.7573, non-silent), clean unload — found that OmniVoice lazily loads an embedded Whisper sub-model on the first `synth()` call when no `ref_text` is given. Alongside this, fixed a real production bug: code-switched Urdu (English loanwords inside Perso-Arabic — UrduSpeech is 57% code-switched) was rejected as `AmbiguousScriptError` before routing ran; `domain/language.py`'s `profile_text()` now treats Latin runs under 75% as loanword islands for languages with a native non-Latin script. Also fixed the model picker rendering maintainer notes as user-facing text (new `ModelSpec.caveat` field, ≤140 chars). **Live-verified end-to-end**: real pod backend tunneled to a local frontend (no ngrok — SSH tunnel + Vite's dev proxy), manual OmniVoice selection generated real audio through the actual job queue, and a fresh code-switched sentence (`...GitHub پر...pull request...`) correctly routed instead of 422ing. Confirmed Roman Urdu → OmniVoice is refused by design (`NoRouteError`, no `(ur, LATIN)` cell declared) — closing that gap was the transliteration probe below, which missed its own gate (both a Devanagari-target and a Perso-Arabic-target retry — see below). `OMNIVOICE_URDU`'s cell flipped to `verified=True` 2026-08-15 (owner's call, on top of the arm Eprod gate re-run clearing on the production backend) — `ModelCatalog.candidates()` now excludes non-permissive licenses even from verified specs, so this still never reaches Auto routing, only an explicit `model_id` request. Full detail: `docs/HANDOFF.md`'s in-flight section, `docs/URDU_BAKEOFF_RESULTS.md`'s closing note. |
| **Roman Urdu → Perso-Arabic conversion (Phase 2 of the Urdu plan)** | 🟢 **GATE PASSED 2026-08-16 on `google/gemma-4-31b-it` (Apache 2.0). Phase B is unblocked.** A3 ran three times. Run 1 (Qwen2.5-7B, 40% contract, CER 0.3061) → *"not usable"*. Run 2 (Ministral-3-8B, 74%, CER 0.0777) → ten reported defects, nine of them text errors of the worst kind: valid Urdu words meaning something else (کال *call* for کل *tomorrow*, طباعت *printing* for طبیعت *health*, بجھ *extinguish* for بھیج *send*). Run 3 (**Gemma-4-31B at 4-bit, 33/45 contract, CER 0.0414, 0 unparseable across all four arms**) → owner: *"perfect with the current data… it's best"*, with **all ten** of run 2's defects fixed. **Note runs 2 and 3 have the same contract rate to within a point and land on opposite sides of the gate** — the fourth demonstration that these metrics can only fail a candidate, never approve one. Prompting is not the lever in either direction: Gemma's four arms are within noise (30–33/45), because it already holds the constraints, exactly inverting §10a where Qwen could not hold them at all. **Two things Phase B must design for, not discover:** Gemma-4-31B is ~19 GB resident against a 24 GB card with `budget_mb = 16000`, so a second scheduler could demand VRAM outside the main scheduler's semaphore and break golden rule 3's guarantee; and the one remaining defect (میٹنگ read as *mating*) is **dictionary** work that arrives already in Perso-Arabic, so entries must be keyable on **either script**, which `_LOANWORD_LEXICON`'s Latin-only keys cannot do. Phase A's byproducts stand: corpus 13 → 45 items, `eval/translit_metrics.py`, and three A3 clip sets at `eval/results/a3_{full_chain,ministral,gemma31b}/` sharing the same ten sentences for runs 1 and 3. Full reasoning: [URDU_BAKEOFF_RESULTS.md §9–§15](URDU_BAKEOFF_RESULTS.md). |
| **Pronunciation dictionary + Studio workflow (titles, non-blocking queue, warm-up)** | ✅ Shipped 2026-08-16/17, backend + frontend, `pytest -m "not gpu"` and `npm run build` both green — **not yet exercised against the pod in a browser** | Two things landed together because the dictionary needed somewhere to live. **The dictionary** closes the §15 gate's one remaining defect and §9e's measured 17.2% loanword failure rate: `pronunciation_entries` table (UNIQUE on `(language, key_text COLLATE NOCASE)`), full CRUD at `/api/pronunciations`, a `Pronunciation` tab, and a **data-driven** `DEFAULT_LOANWORD_LEXICON` that ships `میٹنگ → مِیٹِنگ` — the first Perso-Arabic-keyed entry, proving the either-script requirement is satisfiable. **Golden rule 4 is intact**: the lookup is a `get_lexicon(db)` FastAPI dependency resolved *before* `resolve()` is called, so routing still does no I/O; the merge policy (`effective_lexicon`) is a pure function with its own tests. A disabled user entry **suppresses** a built-in — the only way to turn a shipped default off, which is why the panel shows disabled rows instead of hiding them. **The workflow changes**: generations now carry a 2–3 word title that the analyzer returns in the *same* CLASSIFY response as its prosody rows (one ~6 GB load, not two), falling back to the text's first four words with `source` naming which path ran; Generate enqueues and re-enables immediately (the backend always allowed concurrent jobs — `Composer.tsx` was the only thing serialising them) with a queued toast, an In progress strip, and a settled notification; Recent and History merged into one list; both models warm at startup with a throwaway synthesis each, because OmniVoice's embedded Whisper loads lazily on the first `synth()` and warming weights alone leaves the ~160 s stall in place. Also **the first schema migration this project has ever had** — an add-only `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` pass, because `title` would otherwise have reached a fresh install and silently missed the pod's real database. |
| *(superseded)* Transliteration viability probe — earlier attempts | 🔴 **Ran four times, missed the gate every time (2026-08-15) — escalation path exhausted** | `eval/run_translit_probe.py`, `Qwen/Qwen2.5-3B-Instruct` on the pod, scored Perso-Arabic→Devanagari and Roman→Devanagari against the corpus's hand-authored gold Devanagari. Mean CER 0.2771 / 0.3075 — the plan's own gate ("close to gold or the transform layer isn't built") is not cleared. Retried against the actually-correct target for OmniVoice — Roman→**Perso-Arabic** instead of Devanagari — via `eval/run_roman_arabic_probe.py`: zero-shot mean CER 0.2332, few-shot 0.1942 among parseable cases but 2/5 owner_core items came back unparseable. Also misses the gate. A web search (no code written) found no adoptable non-LLM transliterator — `urduhack` doesn't do Roman→Urdu at all (general NLP toolkit, Python 3.6/3.7-only), the two purpose-built candidates are single-notebook academic projects (4–18 stars, one GPL-3.0), and the literature flags rule-based Roman→Urdu as fundamentally lossy (26 Latin letters, ~44 Urdu sounds). Escalated to `Qwen2.5-7B-Instruct` instead (same script, `PROBE_MODEL_ID` env var, freed the pod's 24GB first): zero-shot mean CER **0.2693 — worse than 3B**, few-shot 0.2029 — about the same as 3B, but zero unparseable responses in either variant (3B had 3, including 2 of the 5 owner_core items). Bigger model bought reliability, not accuracy. **Both escalation paths (bigger model, non-LLM transliterator) are now tried and both miss the gate — `NoRouteError` on Roman Urdu → OmniVoice stays as-is, no further escalation identified.** Full numbers: [URDU_BAKEOFF_RESULTS.md §8/§8b/§8c](URDU_BAKEOFF_RESULTS.md#8-phase-2--transliteration-viability-probe-bench), manifests at `eval/results/roman_arabic_probe/manifest.json` (3B) and `eval/results/roman_arabic_probe_qwen_qwen2_5_7b_instruct/manifest.json` (7B). |

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

**This section was stale** — items 2 and 3 below read as open work but both shipped on `main` days
before this note (`08046b7`, 2026-08-12, and `f4f061a`, 2026-08-13). Caught 2026-08-15 when about to
duplicate them; corrected here rather than left for the next session to rediscover the hard way.

1. **Real-audio pod validation — objective checks passed (2026-08-12), human listen still open.**
   Hit the real `POST /api/generate` with `apply_direction: true` on the pod against a live VoxCPM2
   worker (3-sentence Hindi text mixing neutral/exclamatory/question segments, enrolled profile from
   `eval/fixtures/voice_urdu.wav`), polled the job to `succeeded`, downloaded the resulting WAV
   (`segment_count: 3`, `duration_sec: 4.252`, real 48kHz output — not resampled, matching VoxCPM2's
   own rate as documented). Automated analysis of the actual samples: no NaN, no silence-only regions,
   real RMS energy throughout, **zero sample-to-sample discontinuities above a 0.5 abs-diff click
   threshold anywhere in the file**, and exactly 2 silence runs (~0.25s each) at the 2 expected
   inter-segment boundaries for a 3-segment clip — `concat_wavs_with_pauses` is splicing where the
   directed plan says it should, with no audible-discontinuity signature. **Still the one genuinely
   open item**: not yet confirmed perceptually (does the emphasis/cfg_value difference actually sound
   like a delivery change, independent of what's measurable in the raw samples) — the clip is saved
   locally at `eval/results/direction/pod_directed_hi.wav` (untracked — never committed), awaiting a
   human listen before this line says "verified" rather than "objectively sound."
2. **LLM analyzer (Qwen2.5-Instruct, Apache-2.0) — shipped, frontend included.** Backend landed
   2026-08-12 as described below; frontend wiring landed 2026-08-13 (`f4f061a`): a "Let AI suggest
   emotion/tone" button inside the Advanced editor (item 3) enqueues
   `POST /api/direction/analyze-llm`, polls it with the same `useJob` pattern the main generate job
   uses, and on success merges each classified row into `directionEdits` at the matching segment
   index — text and `pause_after_ms` always stay the heuristic's, the LLM only supplies
   emotion/intensity/energy/rate. Suggest-then-edit, not silent auto-apply: the result lands in the
   same edit state a manual change would, immediately visible/editable/resettable through the
   existing Advanced editor controls. `frontend/src/components/DirectionPanel.tsx`'s
   `onSuggestAi`/`aiSuggestLoading`/`aiSuggestError` props and `Composer.tsx`'s
   `useAnalyzeLlmMutation()` are the wiring; no further frontend work needed here.
   **Capability probe passed (2026-08-12):** `eval/run_qwen_analyzer_probe.py` against
   `Qwen/Qwen2.5-3B-Instruct` (unpinned, probe-only) on the pod — 0 validation problems across 6 cases
   spanning English/Roman-Urdu/Hindi and neutral/happy/sad/anxious/excited/angry, structured-JSON output,
   2.1-3.7s generation per case, 19-27s cold load. Segmentation/emphasis/pause_after_ms stay on the
   existing heuristic; the LLM classifies only emotion/intensity/energy/rate for already-segmented
   sentences. Real pod verification (fresh RunPod instance): direct backend load→classify→unload (0
   problems, en/ur/hi), the full worker-subprocess + `AnalyzerScheduler` path, and the real HTTP path
   (`POST /api/direction/analyze-llm` → 202 → polled `succeeded`, correct rows, one worker reused
   across requests — `load_time_sec` nonzero only on the cold call). A real bug was found and fixed
   during verification: `load_time_sec` wasn't threaded from the LOAD response into the following
   classify call. **Open risk, documented not silently accepted**: the idle-unload timer is the only
   VRAM-contention mitigation between this scheduler and the audio `InferenceScheduler` — they don't
   share a real budget, so a resident Qwen worker (~6GB) alongside a resident audio model on the 20GB
   pod card is a real risk during overlapping requests, not just at idle. Not yet stress-tested under
   a real concurrent request.
3. **Advanced editing — shipped (2026-08-12, `08046b7`).** The full per-segment IR editor (the
   "Advanced tab for pros") is built: `DirectionPanel.tsx`'s `showAdvanced` disclosure renders
   editable selects for emotion/intensity/energy/rate and a numeric `pause_after_ms` per segment
   (deliberately no Tone control — no runtime honors it, same reasoning that got
   Emotion/Style-Exaggeration deleted elsewhere), a per-segment "Reset to detected" and a bulk
   "Reset all edited," and an edited-count badge on the disclosure toggle. Never editable: segment
   text or boundaries — the editor changes prosody only. `Composer.tsx` owns the edit state
   (`directionEdits`, sparse/index-keyed) and the staleness check that clears it if the underlying
   segmentation changes enough that old indices may no longer line up, telling the user why rather
   than silently dropping their edits. Backend contract landed 2026-08-11: `direction_plan` on
   `TTSGenerateRequest` accepts these overrides, re-validated server-side against a fresh `analyze()`
   of the same text (422 on a stale/unknown index) — never a client-controlled segment text or
   emphasis.

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
