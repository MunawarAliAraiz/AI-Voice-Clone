# Architecture

> Supersedes the root `ARCHITECTURE.md`, whose §5 claimed the AI engines were
> unwritten stubs. They were fully implemented — 333 lines in `fish_speech.py`
> alone. X1 deletes the old file in Wave 2. Where docs and code disagree, the
> code wins; that is why this document points at line numbers rather than
> paraphrasing.

Design rationale lives in [REWRITE_PLAN.md](REWRITE_PLAN.md). This describes the
shape the contracts (Wave 0) establish.

---

## The two invariants

Everything else is a detail. These two are not.

### 1. `import torch` must not be reachable from `app.main`

Engine runtimes run in **separate OS processes** behind a synchronous, blocking
interface. There is no coroutine in a worker, so no worker code can block an
event loop — there is no event loop there to block.

```
API process (no torch, no CUDA)            Worker process (torch, CUDA)
┌────────────────────────────────┐         ┌──────────────────────────────┐
│ FastAPI ─ routing ─ scheduler  │ stdio   │ runtime ─ checkpoint ─ CUDA  │
│                       │        │ ◄─────► │                              │
│              worker_client.py  │  JSONL  │        worker.py             │
└────────────────────────────────┘         └──────────────────────────────┘
```

Subprocesses rather than a thread pool, for three reasons a thread pool cannot
give:

1. **Dependency isolation.** F5, Chatterbox and VoxCPM pin `transformers` /
   `torchaudio` stacks with a real chance of not co-resolving. A subprocess can
   run a *different interpreter*. This alone decides it.
2. **VRAM actually returns.** `del model; empty_cache()` leaves fragmentation and
   a ~500 MB CUDA context. `SIGKILL` returns everything, deterministically.
3. **Crash isolation.** A CUDA illegal-access kills one worker, not the API.

IPC is nearly free: audio is already on disk, so messages are a few hundred bytes
of line-delimited JSON. No waveform is ever serialized.

Verified mechanically in Wave 4:

```bash
grep -rn "^import torch\|^from torch" backend/app/
# must match ONLY inference/runtimes/** and inference/worker.py
```

The payoff: the API process needs no CUDA, so CI and the full non-GPU suite run
on a CPU runner in ~30 s, and startup drops ~4 s of torch import.

### 2. Eviction only under the GPU slot

> Eviction happens only inside `_ensure_ready()`, which is only ever called
> while holding the single GPU-slot semaphore.

Nobody can evict without the slot; nobody can infer without the slot. Therefore
unload-during-inference is **unrepresentable**, not merely guarded.

```python
async with _bounded(self._admission):        # -> QueueFullError (503)
    async with self._slot:                   # <-- the invariant lives here
        worker = await self._ensure_ready(spec)
        resp = await asyncio.shield(          # never abandon a live CUDA kernel
            worker.call(WireOp.SYNTH, ..., timeout=self._timeout_for(spec, text)))
```

`asyncio.shield` is load-bearing. If an HTTP client disconnects mid-generation,
an unshielded await would be cancelled, the `async with` would release the slot,
and the next request would enter while the abandoned kernel is still running —
exactly the race this design eliminates. The only early exit is a hard timeout
followed by `SIGKILL`.

The predecessor had **zero** locks (`grep asyncio.Lock|Semaphore|Queue` → 0), and
in its default `ONE_ACTIVE_MODEL` mode request B could `unload_model()` while
request A was mid-inference.

---

## Layers

| Layer | Package | Rule |
|---|---|---|
| HTTP | `app/api/` | Routers, middleware, media tokens. Depends on `SchedulerProtocol`, never on the scheduler. |
| Domain | `app/domain/` | Pure. No I/O, no clock, no torch, no load state. |
| Inference | `app/inference/` | Catalog, scheduler, worker client. Only `runtimes/**` and `worker.py` touch torch. |
| Persistence | `app/db/` | One long-lived aiosqlite connection, write lock, `Depends(get_db)`. |

### Routing is a pure function

```python
def resolve(profile, requested, catalog, strategy) -> RoutePlan
```

No I/O. No `is_loaded`. Routing that consults load state is nondeterministic by
construction and is the root cause of the defect this rewrite exists to kill: the
predecessor picked "the first engine with `is_loaded=True`", which on a cold
server was only ever the mock, so every `engine="auto"` request returned a 440 Hz
sine wave with HTTP 200 — logged as success.

**Routing decides what should run. The scheduler makes it so.**

| language | script | plan |
|---|---|---|
| `ur` | Perso-Arabic | `f5_openbible_urdu`, transform `none` |
| `ur` | Perso-Arabic + `strategy=translit` | `arab→deva` (**lossy**) → best Hindi model |
| `ur` | Latin (Roman Urdu) | `roman→deva` → best Hindi model |
| `hi` | Devanagari | best Hindi model, transform `none` |
| `en` | Latin | best English model, transform `none` |
| anything else | — | `NoRouteError` → **422** |

**The user declares the language; the code detects the script.** Script detection
cannot distinguish Roman Urdu from English — "Aap kaise hain" and "How are you"
are both Latin — so there is no classifier, and there must not be one.
`(language="ur", script=LATIN)` *is* Roman Urdu, because the user said so.
`(ur, DEVANAGARI)` is rejected with a 422 suggesting `language=hi`.

**No silent fallback, ever.** Every response carries `route: {model_id,
transform, lossy, rationale}`, rendered in the UI as a visible chip.

---

## Models: 3 runtimes, 5 specs

| Runtime | Specs | License | Role |
|---|---|---|---|
| `f5` | `f5_openbible_urdu`, `f5_indic`, `f5_openf5_en` | CC-BY-SA / MIT / Apache-2.0 | Urdu, Hindi, English |
| `chatterbox` | `chatterbox_ml_v3` | MIT | Hindi, English |
| `voxcpm` | `voxcpm2` | Apache-2.0 | Hindi, English |

F5 is **one runtime class with three registered specs** — not three classes (the
hard parts — reference trimming to the 8192-frame limit, `۔` chunking, concat —
are identical, and three classes means fixing every F5 bug three times), and not
one opaque "F5" entry (different languages, licenses and quality per checkpoint;
collapsing them reproduces the exact lie the old code told). A checkpoint swap
within a warm runtime is ~1–3 s; a runtime switch is 20–60 s. The scheduler and
the UI both surface that difference.

Removed: XTTS v2 (CPML, non-commercial), Fish Speech (research license), ChatTTS
(`NameError` in `generate()`, and it calls `sample_random_speaker()` — it never
cloned), and the mock.

**Only permissive licenses ship.** `License.is_permissive` gates it and Wave 4
audits `catalog.unshippable()` is empty.

### Nothing routes on a Wave 0 checkout

Every `LanguageSupport` starts `verified=False`, and `ModelSpec.supports()`
returns False for unverified cells. So the catalog resolves nothing until Phase A
measures it — deliberately. Wave 3 sets `verified=True` with real numbers and
**deletes** cells that failed the gate (CER < 25 %, speaker cosine > 0.70,
RTF < 1.0). Advertising a language a model cannot speak is precisely how XTTS
ended up claiming Urdu.

`hf_revision` is likewise `PENDING_PIN` until research resolves each to a 40-char
commit sha. Unpinned `main` plus `trust_remote_code=True` (IndicF5 may need it) is
a supply-chain hole.

---

## VRAM accounting

Budget for the 24 GB A5000:

```
24564 MB total
-  ~500 MB driver
-  ~500 MB CUDA context per worker
- ~2000 MB fragmentation headroom
= 16000 MB  ->  budget_mb=16000, max_workers=2, LRU eviction
```

Free VRAM is read via **NVML / `mem_get_info()`** — never
`total_memory - memory_allocated()`. The latter sees only the calling process's
tensors, and the models live in *other* processes, so it reports 24 GB free while
workers hold 20 GB. `gpu_manager.py:103-104` does exactly that today, and every
admission decision downstream inherits the wrong number.

**One uvicorn worker only.** N workers = N schedulers = N × VRAM.

---

## HTTP contract

- Errors are **RFC 9457** `application/problem+json` with a stable `code`. The
  `{"status": "ok", "result": …}` envelope is gone: success bodies are the
  resource, and status codes distinguish them.
- **Every** route declares `response_model=`. The predecessor had exactly one,
  and 12 of its 15 schemas were dead.
- **Middleware order.** Starlette runs the last-added middleware *outermost*, so
  the API-key middleware is added **first** (innermost) and CORS **last**
  (outermost). The old order put the key check outside CORS, so a preflight
  `OPTIONS` — which carries no `X-API-Key` — got a 403 with no CORS headers, and
  every cross-origin write broke the moment `VCS_API_KEY` was set.
- Explicit CORS origin list. Boot is refused if `api_key` is set while origins
  contain `*`. Key comparison uses `hmac.compare_digest`.
- **Media tokens.** `<audio>` elements cannot send auth headers, so
  `GET /api/media/history/{id}?t=<hmac>.<exp>` returns a `FileResponse` (Range
  works, so seeking works). The signed URL is embedded in every response; the
  frontend never builds one. Blob-fetching was rejected: it forces a full
  download, breaks seeking, and pins the file in memory.
- **No duplicate routes.** `routers/models.py`'s endpoints were unreachable
  because `settings.py` registered the same paths first. Startup asserts on
  duplicate `(method, path)` and refuses to boot.

---

## Frontend

TanStack Query v5 for server state (~90 % of the state here), plus a ~60-line
Zustand store for the three things that are genuinely client state: API key,
toast queue, record device preference. Query makes `isLoading`/`isError`
impossible to omit, which removes all six `.catch(() => {})` sites at once.

`types/api.ts` is generated from OpenAPI — never hand-edited — so wire types
cannot drift. All query keys come from `lib/queryKeys.ts`; inline key literals
are how invalidation silently stops working.

The model picker is driven entirely by `GET /api/models`: languages, license,
residency and `est_wait_sec` ("OpenBible Urdu · CC-BY-SA · cold, ~40 s"), shown
*before* the click. `dir="rtl"` keys off the **detected script**, never
`language === 'ur'`, which wrongly right-aligns Roman Urdu.

---

## Testing

```bash
cd backend && pytest -m "not gpu" -q     # CPU-only, no torch, ~30s
cd backend && pytest -m gpu -q           # pod only: real subprocess, real weights
cd frontend && npm run test && npm run build
```

The scheduler tests carry the value and **none needs a GPU**, because
`tests/fakes/FakeWorker` sits behind the same protocol and instruments the things
that matter: `load_calls`, `max_concurrent`, `unload_during_synth`.

```python
test_no_unload_during_inference          # the ONE_ACTIVE_MODEL use-after-free
test_no_double_load                      # 10 concurrent -> load_calls == 1
test_queue_bounded                       # 64 concurrent -> QueueFullError, not OOM
test_event_loop_never_blocks             # 10ms heartbeat, assert no gap > 50ms
test_client_disconnect_does_not_release_slot_early
test_options_preflight_succeeds_with_api_key
```

**The regression that must never return:** a request that returns HTTP 200 with
audio no model produced.
