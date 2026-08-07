# AI Voice Clone Studio — Engine Consolidation & Performance Rewrite

> **Provenance:** authored 2026-08-03 in the RunPod session "AI Voice Clone project inspection"
> (opus-5, high effort) and recovered 2026-08-04 after that pod was terminated. This file is the
> canonical copy. Commit it — do not let it live only on a pod again.

## Context

**Why:** The app is a FastAPI + React voice-cloning studio with four TTS engines. An audit of `origin/dev` found the product is not shippable in its current form:

- **It can silently lie.** `select_engine_for_language()` gates on `is_loaded`, and no real engine loads eagerly — so the first `engine="auto"` request on a cold server returns a **440 Hz sine wave with HTTP 200**. Every layer above is untrustworthy while this exists.
- **Nothing is offloaded.** Exactly 3 `asyncio.to_thread` calls exist repo-wide. Model loading, torch inference, 3+ ffmpeg spawns, MP3 encode, and multi-GB `snapshot_download` all run on the event loop. One request freezes the single-worker server.
- **Zero concurrency control.** `grep asyncio.Lock|Semaphore|Queue` → 0 results. In default `ONE_ACTIVE_MODEL` mode, request B can `unload_model()` while request A is mid-inference — use-after-free on CUDA memory.
- **Licensing is a shipping blocker.** XTTS v2 is CPML non-commercial; Fish Speech is a research license. Both must go.

**Outcome:** three permissively-licensed engines, an inference architecture where blocking work is *structurally impossible* on the event loop, real Urdu + Hindi support with the transformation made visible to the user, and a test suite where none of the above can regress.

### Decisions locked (from user)

| | |
|---|---|
| Base branch | `origin/dev` (has the real engine fixes; note it does **not** contain `main`'s last 2 commits) |
| Licensing | **Free/permissive only** — no paid tiers, no CC-BY-NC weights |
| Sequencing | **Validate engines first**, then rewrite |
| Desktop shell | **Drop Tauri** — web-only (`src/` has zero Tauri imports, so this is free) |
| Urdu | **Both Perso-Arabic and Roman Urdu** |
| Mock engine | **Removed from the product.** Survives only as `tests/fakes/FakeRuntime`. |

### The honest finding on Urdu

No truly-free model does Urdu zero-shot cloning *well*. The good ones — OmniVoice (211 h Urdu), Fish s2-pro, Higgs 3 — are all non-commercial and therefore excluded. Exactly one free native-Urdu cloning checkpoint exists: **`multilingual-tts/F5-TTS-OpenBible-Urdu`** (CC-BY-SA-4.0, trained from scratch so it does *not* inherit F5's CC-BY-NC). It is Bible-domain read speech — formal register, narrow prosody.

So Urdu ships via **two paths**, and the plan treats the second as a first-class feature rather than a hack:
1. **Native** — Perso-Arabic → OpenBible-Urdu checkpoint.
2. **Transliterated** — Roman Urdu (and optionally Perso-Arabic) → Devanagari via `ai4bharat-transliteration` (MIT) → a Hindi engine. Linguistically sound: Hindi and Urdu are the same spoken language.

Phase A decides which wins per case, empirically. If a cell fails the quality gate, that language is **removed from the catalog** rather than advertised.

---

## Engines: 3 runtimes, 5 model specs

| Runtime | Model specs | License | Role |
|---|---|---|---|
| `f5` | `f5_openbible_urdu`, `f5_indic`, `f5_openf5_en` | CC-BY-SA / MIT / Apache-2.0 | **Urdu**, Hindi/Indic, English |
| `chatterbox` | `chatterbox_ml_v3` | MIT | Hindi + 23 langs, English quality |
| `voxcpm` | `voxcpm2` | Apache-2.0 | Hindi + 30 langs |

**F5 is one runtime class with three registered specs** — not three classes (the hard parts: reference trimming to the 8192-frame limit, `۔` chunking, concat, are identical; three classes means fixing every F5 bug three times) and not one opaque "F5" entry (different languages, licenses, and quality per checkpoint — collapsing them reproduces exactly the lie the current code tells). Checkpoint swap within a warm process is ~1–3 s; a runtime switch is 20–60 s. The scheduler and the UI both surface that difference.

**Removed:** XTTS v2 (CPML), Fish Speech (research license), ChatTTS (`NameError` in `generate()`, and it calls `sample_random_speaker()` — it never clones), mock.

---

## Phase A — Validate before rewriting (the "Stage 2" deliverable)

Runs in `/workspace/engines-lab/<agent>/`, **never in the repo** — four agents pip-installing conflicting engines into one tree is how `origin/dev` got its `fish_speech`/`audiotools` commit sequence.

Mandatory env in every research prompt (the 30 GB overlay will otherwise fill and kill the pod):

```bash
export HF_HOME=/workspace/hf-cache TORCH_HOME=/workspace/torch-cache \
       PIP_CACHE_DIR=/workspace/pip-cache TMPDIR=/workspace/tmp
python3 -m venv --system-site-packages /workspace/engines-lab/<name>/.venv
```

**Serialize GPU access** via `flock /workspace/engines-lab/.gpu.lock` — concurrent measurement produces garbage VRAM numbers.

| Agent | Scope | Key unknown to resolve |
|---|---|---|
| R1 (Haiku) | F5 + 3 checkpoints | **How does `ai4bharat/IndicF5` actually load** — raw ckpt vs `AutoModel(trust_remote_code=True)`? Decides the loader strategy. |
| R2 (Haiku) | Chatterbox ML v3 | Real param names (`exaggeration`, `cfg_weight`), verified language list |
| R3 (Haiku) | VoxCPM2 | Confirm ~8 GB / RTF 0.30 **on A5000 sm_86** (published figures are 4090/Ada) |
| R4 (Sonnet) | Urdu pipeline + eval harness | **Does `ur` exist in `ai4bharat-transliteration`'s indic→roman direction?** If not, the Perso-Arabic→Devanagari path collapses to NLLB translation only. Coin flip. |

**Gate — must pass before the routing table is finalized.** Per claimed (engine × language × script) cell: 3 sentences of 15–40 words, one real 10 s human reference. Require **CER < 25%** (Whisper-large-v3), **speaker cosine > 0.70**, **RTF < 1.0**. Any failing cell is deleted from `catalog.py`. *Do not ship a language a model cannot actually speak* — that is precisely how XTTS ended up claiming Urdu.

Deliverable: `docs/PHASE_A_RESULTS.md` + real numbers folded into `catalog.py`, and **audible proof** of an Urdu, Hindi, and English clone on this A5000.

---

## Phase B — Architecture

### B1. The structural invariant

> **`import torch` must not be reachable from `app.main`.**

Engine runtimes live in **separate OS processes**, with a **sync, blocking, boring** interface. There is no coroutine in the engine layer, so nothing can accidentally block. Consequences: API startup drops ~4 s of torch import; the API process needs no CUDA at all, so CI and unit tests run on a CPU runner in ~30 s.

Chosen over a `ThreadPoolExecutor` for three reasons that a thread pool cannot give:
- **Dependency isolation.** F5, Chatterbox, and VoxCPM pin `transformers`/`torchaudio` stacks with a real chance of not co-resolving. A subprocess can use a *per-runtime interpreter*; `multiprocessing` cannot. This alone decides it.
- **VRAM actually returns.** `del model; empty_cache()` leaves fragmentation and a ~500 MB CUDA context. `SIGKILL` returns everything, deterministically.
- **Crash isolation.** A CUDA illegal-access kills a worker, not the API.

IPC cost is near zero: audio already lives on disk, so payloads are a few hundred bytes of JSON over line-delimited stdio. No waveform serialization.

### B2. Scheduler — the invariant that kills the use-after-free

> **Eviction happens only inside `_ensure_ready()`, which is only ever called while holding the single GPU-slot semaphore.**

If nobody can evict without the slot, and nobody can infer without the slot, unload-during-inference is *unrepresentable* — one sentence of reasoning instead of a lock hierarchy.

```python
class InferenceScheduler:
    self._slot      = asyncio.Semaphore(1)   # THE GPU. exactly one holder.
    self._admission = asyncio.Semaphore(8)   # bounded queue -> 503, not OOM

    async def synthesize(self, req):
        async with _bounded(self._admission):
            async with self._slot:                       # <-- invariant lives here
                worker = await self._ensure_ready(spec)  # safe to evict: we hold the slot
                resp = await asyncio.shield(             # never abandon a live CUDA kernel
                    worker.call("synth", ..., timeout=self._timeout_for(spec, req.text)))
                return SynthResult(**resp["result"])
```

`asyncio.shield` matters: if the HTTP client disconnects mid-generation, cancelling would drop the slot while a kernel still runs — the exact race being eliminated. The only early exit is hard timeout → `SIGKILL`.

**VRAM budget (24 GB A5000):** 24 564 − ~500 driver − ~500/worker CUDA ctx − ~2 000 fragmentation headroom → **`budget_mb = 16 000`, `max_workers = 2`**, LRU eviction. Free VRAM read via **NVML / `mem_get_info()`**, never `total - memory_allocated()` (which sees only this process's tensors and reports 24 GB free while another process holds 20 GB — every current OOM decision is built on that wrong number).

Expose `state: resident | warm | cold` + `est_load_sec` on `/api/models` so the UI can say "~40 s to load" *before* the click.

### B3. Language routing — pure function, explicit failure

```python
def resolve(profile: TextProfile, requested: str | None,
            catalog: ModelCatalog, strategy: UrduStrategy) -> RoutePlan
```

`resolve` is **pure** — no I/O, no `is_loaded`. Routing that consults load state is nondeterministic by construction and is the root cause of the sine-wave bug. Routing decides *what should run*; the scheduler makes it so.

**Script detection cannot distinguish Roman Urdu from English** — "Aap kaise hain" and "How are you" are both Latin. Do not build a classifier. **The user declares the language; the script is detected.** `(language="ur", script=LATIN)` *is* the definition of Roman Urdu, unambiguously. Reject `(ur, DEVANAGARI)` with a 422 suggesting `language=hi`.

| language | script | plan |
|---|---|---|
| `ur` | Perso-Arabic | `f5_openbible_urdu`, transform `none` |
| `ur` | Perso-Arabic (`strategy=translit`) | `arab→roman→deva` (**lossy**) → best Hindi model |
| `ur` | Latin (Roman Urdu) | `roman→deva` (one hop) → best Hindi model |
| `hi` | Devanagari | `chatterbox_ml_v3` / `voxcpm2` / `f5_indic` |
| `en` | Latin | `f5_openf5_en`, `chatterbox_ml_v3` |
| other | — | whichever model declares it, else **422** |

**No silent fallback, ever.** `NoRouteError` → 422 whose body enumerates exactly what *would* work. Every response carries `route: {model_id, transform, lossy, rationale}`, rendered in the UI as a visible chip — a user must never wonder why their Urdu came out sounding like Hindi.

Reuse: `translation_service.py:39-61` script detection (correct Unicode ranges) → `app/domain/language.py`. `f5_tts.py:99`'s `۔` handling → `app/domain/text.py::split_sentences`, extended with `؟ ، ।`, tested on all three scripts — it's currently trapped inside the F5 engine where the other two runtimes can't reach it.

### B4. API layer

- **Middleware order is a live bug.** `main.py:104` adds CORS, then `:115` adds the API-key check — Starlette runs last-added *outermost*, so the key check runs **before** CORS. A preflight `OPTIONS` carries no `X-API-Key` → 403 with no CORS headers. **Setting `VCS_API_KEY` today breaks every cross-origin write.** Fix: add `APIKeyMiddleware` *first* (innermost), CORS *last* (outermost). Explicit origin list; refuse to boot if `api_key` is set and origins contain `*`. `hmac.compare_digest`.
- **Media tokens** solve the `<audio>` problem: media elements cannot send auth headers. `GET /api/media/history/{id}?t=<hmac>.<exp>` returns a `FileResponse` (Range → seeking works). The signed `audio_url` is embedded in every response, so the frontend never builds one. Prefer this over blob-fetching, which forces a full download, breaks seeking, and pins the file in memory.
- **DB:** one long-lived aiosqlite connection, PRAGMAs applied once, `asyncio.Lock` on writes, `Depends(get_db)`. SQLite serializes writes anyway — a pool buys nothing and costs deadlocks. Kills the per-call connect, the repeated PRAGMAs, and the `translation_service.py:164-178` leak. Add indexes on `generation_history(created_at DESC)`, `(profile_id)`, `voice_profiles(is_active)`.
- **Uploads:** stream to disk in 1 MiB chunks with a running size cap (`max_upload_size_mb` is currently defined and never enforced; `voice.py:29` reads unbounded into RAM), `Content-Length` pre-check, magic-byte sniff after write, `ffprobe` for real duration enforcing min *and* max, delete the raw upload after conversion (every non-WAV upload currently leaks its original forever).
- **`response_model=` on every route** (currently exactly one exists; 12 of 15 schemas are dead). Drop the `{"status":"ok","result":…}` envelope; errors become RFC 9457 `problem+json` with a stable `code`. Generate `frontend/src/types/api.ts` from OpenAPI so wire types cannot drift.
- **Route de-dup:** `routers/models.py`'s `/models`, `/{n}/health`, `/{n}/unload` are currently **unreachable** — shadowed by `settings.py`, registered first. Move them; add a startup assertion that fails fast on any duplicate `(method, path)`.
- **Delete the fake download progress** (`model_manager.py:240-245` ticks 10→90 over 2.5 s *before* the download starts) and the checksum verifier that returns `valid: True` unconditionally. Real byte progress from `snapshot_download(tqdm_class=…)` in the aux worker. **Pin every HF `revision`** — unpinned `main` with `trust_remote_code=True` (IndicF5) is a supply-chain hole.

### B5. Frontend

**TanStack Query v5 + a small Zustand store.** ~90% of state here is *server* state; React Query makes `isLoading`/`isError` impossible to omit, which deletes all six `.catch(() => {})` sites and the missing-loading-state problem in one move. Zustand holds only three slices (~60 lines): API key, toast queue, record device preference.

- **Dynamic engine ↔ language UI** (explicit user requirement). Port `origin/feature/upgrade-tts-engines:GeneratePage.tsx:39-65` wholesale, but extract into a pure, unit-testable `lib/modelSelection.ts`. Extend it: show **license + state + `est_load_sec`** per model ("OpenBible Urdu · CC-BY-SA · cold, ~40 s"), bidirectional filtering with `reconcile()`, live debounced script detection in the textarea warning *before* generation, `dir="rtl"` keyed off **detected script** not `language==='ur'` (which wrongly right-aligns Roman Urdu).
- **Record UX** — split the 13 KB single component into `useRecorder` + `useAudioAnalyser` + `WaveformCanvas` / `LevelMeter` / `DevicePicker` / `TrimEditor` / `Dropzone`. Priority fixes: close `AudioContext` on every exit path (browsers cap ~6; the 7th recording currently fails silently), revoke object URLs, DPR-correct canvas via `ResizeObserver`, probe `MediaRecorder` codecs (hardcoded `audio/webm` throws on Safari), **level meter + clipping indicator** (reference quality dominates clone quality more than any model param), min-3 s guard, and **trimming** — F5 hard-trims the reference to ~6 s, so *which* 6 s is currently arbitrary.
- **Two confirmed live bugs to fix:** three CSS vars used but never declared (`--color-primary`, `--border-color`, `--transition-medium`) leave the Record/Upload tab with **no visible active state** — fix by renaming consumers to the real tokens, plus a CI check that greps every `var(--x)` against `variables.css`. And `.stagger-item`'s eight hand-unrolled `nth-child` rules leave **History rows 9–15 permanently at `opacity: 0`** — replace with a `calc(min(var(--stagger-i), 12) * 50ms)` delay, with a `prefers-reduced-motion` block guaranteeing `opacity: 1`.
- **A11y from zero** (`grep "aria-|role=|htmlFor"` → 0 matches): `<Field>` wrapper producing real label associations, tabs → `role="tablist"` with arrow keys, dropzone → real `<button>` (unreachable by keyboard today), emoji icons → `aria-hidden` + text, `aria-live` regions. Gate with `vitest-axe` + one `@axe-core/playwright` smoke.
- **Keep:** `styles/variables.css` (a genuinely good token system — add only `--focus-ring`, `--z-*`), the `voiceApi/ttsApi/historyApi/systemApi` module shape, the mobile sidebar→bottom-nav transform, the AnalyserNode DSP math.

### B6. Tests

Delete the three `backend/test_*.py` — throwaway scripts, and `test_vocal_music_separation.py` asserts music separation on a signal containing only a bare 440 Hz sine, so it cannot fail.

Backend: pytest + pytest-asyncio + httpx `ASGITransport`. The scheduler tests carry the value, and **none needs a GPU** thanks to `FakeWorker` behind the same protocol:

```python
async def test_no_unload_during_inference(...)   # the ONE_ACTIVE_MODEL use-after-free
async def test_no_double_load(...)               # 10 concurrent -> load_calls == 1
async def test_queue_bounded(...)                # 64 concurrent -> QueueFullError, not OOM
async def test_event_loop_never_blocks(...)      # 10ms heartbeat, assert no gap > 50ms
async def test_client_disconnect_does_not_release_slot_early(...)
```

Plus `test_api_auth.py::test_options_preflight_succeeds_with_api_key` (the §B4 middleware bug) and `test_config.py` (derived paths must follow `VCS_DATA_DIR` — they're currently frozen at import, so the Docker `/` bug can't even be worked around by env var).

`@pytest.mark.gpu` suite deselected by default; `addopts = "-m 'not gpu'"`. Frontend: Vitest + Testing Library + MSW, incl. a **regression asserting all 15 History rows render with non-zero opacity**. One Playwright smoke: record → save → generate → play — the only test that would have caught the `<audio>` auth problem.

CI: `backend` (ruff + mypy + non-GPU pytest, CPU-only, ~30 s), `frontend` (tsc + eslint + stylelint + vitest + build), `gpu` (manual/nightly on the A5000).

---

## Swarm execution

**The rule that makes parallelism safe: contracts are written first, by one agent, and merged before any parallel group starts.** Every swarm collision is two agents disagreeing about an interface. Wave 0 is deliberately *not* parallel.

**Wave 0 — Contracts (Opus, sequential).** Branch `rewrite/contracts` off `origin/dev`. Produces `inference/{spec,protocol,catalog}.py`, full `exceptions.py` hierarchy + HTTP mapping, all `api/schemas/**`, `db/schema.sql`, generated `types/api.ts`, `lib/queryKeys.ts`, `docs/ARCHITECTURE.md`, plus signature-complete `NotImplementedError` stubs for `scheduler.py` / `routing.py` / `worker_client.py` / `FakeScheduler`. ~600 lines. Nothing else starts until it merges.

**Wave 1 — Research (Haiku ×3 + Sonnet ×1)** — R1–R4 above, `/workspace/engines-lab/` only, zero repo writes.

**Wave 2 — Build (parallel, exclusive directory ownership).** An agent needing a change outside its directory files a contract amendment; it does not edit.

| id | model | owns (exclusive) |
|---|---|---|
| **X1** | Haiku | Deletions: `src-tauri/`, `backend/test_*.py`, `config/default.yaml`, tauri + `pyyaml` deps, docs rewrite. **Runs and merges first** — touches nothing anyone else owns. |
| **B1** | Sonnet | `inference/{scheduler,worker_client,worker}.py`, `runtimes/**`, `gpu_probe.py`, scheduler tests |
| **B2** | Sonnet | `main.py`, `config.py`, `deps.py`, `db/**`, `api/**`, `voice_service`, `media_token`, API tests. Wires B1 behind `SchedulerProtocol` — never imports its implementation. |
| **B3** | Sonnet | `domain/**`, `translit_service`, `translation_service`, `asr_service`, `audio/pipeline.py`, unit tests |
| **F1** | Sonnet | `lib/`, `api/`, `hooks/`, `providers/`, `vite.config.ts`, `package.json` |
| **F2** | Sonnet | `components/{ui,audio}/**`, `styles/**` |
| **F3** | Sonnet | `pages/{Dashboard,Generate,History,Settings}*` |
| **F4** | Sonnet | `pages/Record*` + the four audio components (**after F2 lands their shells**) |
| **D1** | Haiku | `Dockerfile`, `docker-compose.yml`, `requirements/**`, CI workflows |

Only sequential edge in Wave 2: **F2 → F4**. B2 depends on B1's *protocol*, not its code.

**Wave 3 — Integration (Sonnet).** Fold Phase A numbers into `catalog.py` (real VRAM, real language tuples, pinned revisions); amend `routing.py` for any failed cell; implement the three real runtimes **against R1–R3's verified snippets** — B1 leaves them stubbed until here. Merge order `X1 → B3 → B1 → B2 → F1 → F2 → F4 → F3 → D1`. Then the GPU suite end-to-end plus a 20-request soak asserting no VRAM growth.

**Wave 4 — Review (Opus, checklist not vibes).**
- `grep -rn "^import torch\|^from torch" app/` returns **only** `inference/runtimes/**` and `inference/worker.py` — the single strongest structural invariant.
- No `async def` calls a blocking function (`subprocess`, `open`, `.read()`, `time.sleep`).
- Every eviction site provably under `self._slot`.
- **No code path returns audio without a real model having produced it.**
- Every route has `response_model` + error mapping; `grep "catch(() => {})" frontend/src` → 0.
- Security: CORS list, `compare_digest`, media-token expiry, path containment on every `FileResponse`, no `trust_remote_code` on an unpinned revision.
- License audit: every `ModelSpec.license` matches its HF card; zero CC-BY-NC weights; `NOTICE` carries CC-BY-SA attribution for OpenBible-Urdu.

### Flagged as bad ideas
- **Letting B1 write real runtimes from documentation** instead of R1–R3's verified snippets. Every engine-integration bug in this repo's history came from exactly that.
- **Haiku on the scheduler.** A subtly wrong `await` costs a week. Sonnet minimum, Opus review mandatory.
- **Re-adding a mock/demo engine "just for the demo."** If a no-GPU demo is needed: `FakeRuntime` behind `VCS_ALLOW_FAKE_RUNTIME=1`, a loud UI banner, and an `X-Fake-Audio: true` header.
- **More than one uvicorn worker** (N workers = N schedulers = N × VRAM).
- **Keeping `emotion`/`style`** because the dropdown exists. Nine presets resolving to an `atempo` multiplier, with text preprocessing that is a **no-op on Urdu and Hindi** — the app's target languages — is a feature surface with no feature behind it. Replace with real per-model `params` (Chatterbox's `exaggeration`/`cfg_weight`), UI hiding controls the selected model lacks.

---

## Verification

**Phase A gate** — audible Urdu (Perso-Arabic), Roman Urdu, Hindi, and English clones generated on this A5000, each meeting CER < 25% / speaker cosine > 0.70 / RTF < 1.0. Recorded in `docs/PHASE_A_RESULTS.md`.

**Phase B:**

```bash
cd backend && pytest -m "not gpu" -q        # CPU-only, no torch, ~30s
cd backend && pytest -m gpu -q              # real subprocess + real weights on the A5000
cd frontend && npm run build      # tsc -b + vite build (no test script yet)
```

End-to-end on the pod: start the API, `POST /api/voice/upload` a 10 s reference, then generate in all four language/script combinations and confirm each response's `route` chip matches the audio produced. Soak: 20 concurrent requests → no VRAM growth, no worker restarts, no request served by a model that wasn't loaded.

**The regression that must never return:** a request that returns HTTP 200 with audio no model produced.

---

## Critical files

- `backend/app/engines/registry.py` — check-then-act VRAM race → replaced by `inference/scheduler.py`
- `backend/app/services/tts_service.py` — the entire blocking generate path (lines 109, 127, 136, 147, 155)
- `backend/app/engines/f5_tts.py` — only real engine on `dev`; lines 79–117 salvageable, line 162 is the hidden-Whisper trap
- `backend/app/main.py` — inverted middleware order, `"*"` + credentials, non-constant-time key, router shadowing
- `backend/app/utils/gpu_manager.py` — wrong VRAM math (line 106) feeding every OOM decision
- `frontend/src/pages/GeneratePage.tsx` (`origin/feature/upgrade-tts-engines`) — the dynamic filtering to port
- `frontend/src/styles/variables.css` — keep verbatim
