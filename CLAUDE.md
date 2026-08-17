# AI Voice Clone Studio

FastAPI + React voice-cloning studio. Target languages: **Urdu (Perso-Arabic + Roman), English**.
Hindi was fully removed as a target language (catalog, routing, frontend, docs) — see git history
around the removal commit if VoxCPM2's dropped Hindi cells (CER 0.086, speaker cosine 0.887) are ever
needed again as a reference point.

The rewrite is complete and validated end-to-end on GPU with real cloned audio. The design rationale is
**[docs/REWRITE_PLAN.md](docs/REWRITE_PLAN.md)** — read it before changing anything in
`backend/app/inference/`, `backend/app/domain/`, or the engine layer. This file is the operational summary.

**Transcript import + the Hindi question:** **[docs/TRANSCRIPT_IMPORT.md](docs/TRANSCRIPT_IMPORT.md)** —
YouTube caption import, the SSRF guard that governs it, and why **Hindi is a source format here and
never a target language**. Read it before touching `domain/youtube.py`,
`api/routers/transcript.py`, or anything that wants to make Devanagari routable.

**What's currently in flight, and what's next:** **[docs/ROADMAP.md](docs/ROADMAP.md)** —
phase-by-phase status (done / in progress / designed-not-built), including the async job queue,
the Recent tab, mobile/perf fixes, and the deferred Speech Direction / client-side-extraction /
Chatterbox designs. **[docs/HANDOFF.md](docs/HANDOFF.md)** is the lower-level "resume without
reconstructing anything" state dump (pod facts, open items, what a fresh session should do first).
Update both at every checkpoint — a previous pod termination lost a day of planning because state
lived only in a chat transcript, not in git.

**Base branch is `main`.** The rewrite was merged into it on 2026-08-06 (`rewrite/contracts` points at the
same commit and is kept in step). The merge was an *ours* merge: `main`'s two pre-rewrite commits are
preserved as ancestors, but none of their content was reinstated — they only touched deleted predecessor
code, including the CPML-licensed `xtts_v2.py`, which rule 6 forbids reintroducing.
`dev` and `feature/upgrade-tts-engines` are superseded; do not branch from them.

---

## Golden rules

1. **Never return audio a real model did not produce.** The predecessor code shipped a 440 Hz sine wave with
   HTTP 200 when no engine was loaded. The mock engine is deleted. If you need a no-GPU demo, it is
   `tests/fakes/FakeRuntime` behind `VCS_ALLOW_FAKE_RUNTIME=1` with an `X-Fake-Audio: true` header — never a
   silent fallback.
2. **`import torch` must not be reachable from `app.main`.** Engine runtimes live in separate OS processes.
   This is what keeps the API CPU-only, so tests run on a machine with no GPU.
   Enforced by `tests/test_contracts.py::test_no_torch_outside_runtimes`. By hand, **allow leading
   whitespace** — the legacy engines import torch *inside functions*, so an anchored `grep "^import torch"`
   reports them clean while the invariant is broken:
   `grep -rnE "^[[:space:]]*(import torch|from torch)" backend/app/`
3. **Eviction only while holding the GPU-slot semaphore.** This makes unload-during-inference
   unrepresentable rather than merely guarded. **Amended 2026-08-17:** there are now exactly TWO
   eviction call sites — `_ensure_ready()` and `InferenceScheduler.exclusive_gpu()`, the latter
   added because Phase B's Gemma-4-31B is ~19 GB against a 24 GB card and needs the whole thing.
   Both hold the same semaphore and both go through the same `_evict` behind the same assertion, so
   what the rule buys is intact; the reasoning is in `scheduler.py`'s module docstring. **Do not add
   a third** — and note the original wording ("only inside `_ensure_ready`") is what makes this an
   amendment worth arguing rather than a detail.
4. **Routing (`resolve()`) is pure.** No I/O, no `is_loaded`. Routing that consults load state is what caused
   rule 1's bug. Routing decides what *should* run; the scheduler makes it so.
5. **No silent fallback.** Unroutable request → `NoRouteError` → 422 listing what *would* work. Every response
   carries `route: {model_id, transform, lossy, rationale}` and the UI renders it as a visible chip.
6. **Permissive by default; CC-BY-NC allowed for personal use, badged.** No paid tiers, ever. Every
   `ModelSpec.license` must match its HF card **checked separately from the repo's code license** — two
   candidates in this project's own licensing survey (`docs/URDU_MODEL_LICENSING.md`) differed between the
   two, and both times the permissive-looking answer was wrong. **Amended 2026-08-15:** no permissively-licensed
   model both lists Urdu and clones from reference audio (surveyed exhaustively), so `License.CC_BY_NC` may
   appear in the catalog for the owner's own use behind `VCS_API_KEY` — never for a shipped product. Gated by
   `License.personal_use_ok` (`backend/app/inference/spec.py`); `ModelSummary.commercial_use` surfaces it as a
   distinct "Non-commercial" badge in the picker, independent of the `experimental` badge. `License.RESEARCH_ONLY`
   is the one tier stricter than that and stays fully excluded — it's why Higgs Audio v3 (Boson Research &
   Non-Commercial, stricter than CC-BY-NC) is still unintegrated despite being in the bake-off. XTTS v2 (Coqui
   CPML) and Fish Speech (research license) stay banned independently of this amendment — don't reintroduce them.
7. **Pin every HuggingFace `revision`.** `trust_remote_code=True` on an unpinned `main` is a supply-chain hole
   (IndicF5 needs it).
8. **The `jobs` table is the queue.** `POST /api/generate` enqueues a row and returns 202;
   `backend/app/inference/scheduler.py` is **never modified** for job-queue work — no FIFO object
   goes into it, ever. A `JobRunner` (`app/jobs/runner.py`) claims rows from the `jobs` table with an
   atomic `UPDATE ... RETURNING`. Route is resolved once, at enqueue, and stored on the row
   (`route_json`) — a handler calling `resolve()` again at claim time is rule 4's bug wearing a queue.
   A fake-runtime job's completed response must carry both the `X-Fake-Audio: true` header **and**
   `is_fake: true` in the body (rule 1) — the header alone doesn't survive a proxy that strips it.
   Full design: [docs/ROADMAP.md](docs/ROADMAP.md#part-a--async-job-queue).

---

## Workflow: build on the pod, push often

The pod is the primary development environment — it is Linux, fast, has the GPU, and installs there take
seconds rather than minutes. The Windows machine holds the Claude session history and project memory, and is
where planning happens; it is not where the build runs.

```
remote pod (RTX A5000)  --git push-->  GitHub  <--git fetch--  local (reference/planning)
```

**The rule that replaces "never edit on the pod": commit and push from the pod frequently.** A previous pod was
terminated and took a day of planning with it, and the only reason that work survived is that the chat
transcript is stored server-side. Git is the backup — not `/workspace`.

- **Push at every meaningful checkpoint**, not at the end of a session. A pod can vanish without warning.
- **Anything produced on the pod that matters** — `docs/PHASE_A_RESULTS.md`, measured VRAM numbers, verified
  loader snippets — is committed and pushed, never left on disk.
- `/workspace` is a RunPod network volume that has survived a pod termination once. Treat that as luck.
  `/` is a 30 GB ephemeral overlay and is wiped.
- Git credentials live in `/root/.git-credentials` (ephemeral, `chmod 600`) — deliberately **not** in
  `/workspace`, which persists and gets snapshotted. They die with the pod, which is the correct lifetime.
- Redirect all caches to `/workspace` or the overlay fills and kills the pod:
  ```
  export HF_HOME=/workspace/hf-cache TORCH_HOME=/workspace/torch-cache \
         PIP_CACHE_DIR=/workspace/pip-cache TMPDIR=/workspace/tmp
  ```

### What runs where

| Runs locally (no GPU) | Needs the pod |
|---|---|
| Wave 0 contracts; all of `domain/`, `api/`, `db/`, `config` | Phase A engine validation (R1–R4) |
| The scheduler **and all its tests** (`FakeWorker`, no GPU by design) | The three real runtime implementations |
| Entire frontend: build, Vitest, Playwright | `pytest -m gpu` |
| `pytest -m "not gpu"` (~30 s) | 20-request VRAM soak, end-to-end audio checks |

Engine experiments run in `/workspace/engines-lab/<name>/` with its own venv — **never in the repo tree**.
Serialize GPU access with `flock /workspace/engines-lab/.gpu.lock`; concurrent runs produce garbage VRAM numbers.

---

## Remote access

Current pod address, port, and key live in **`.claude/remote.local.md`** (gitignored — the endpoint changes
every pod, and a root SSH endpoint does not belong in a shared repo). Read that file for the live values.

**Use Windows `ssh.exe`, not Git Bash `ssh`.** The private key is passphrase-protected and loaded into the
Windows ssh-agent; Git Bash has no `SSH_AUTH_SOCK` and will fail with `Permission denied (publickey)`.

To run a multi-line script remotely, pipe it over stdin — inline quoting gets mangled by PowerShell, and files
written on Windows carry a UTF-8 BOM and CRLF that make `bash` emit `$'\r': command not found`:

```bash
sed '1s/^\xEF\xBB\xBF//' script.sh | tr -d '\r' | /c/Windows/System32/OpenSSH/ssh.exe -p <PORT> root@<HOST> "bash -s"
```

---

## Environments

| | Local (planning/reference) | Pod (primary dev + test) |
|---|---|---|
| OS | Windows 11 | Ubuntu 24.04.3 |
| Python | 3.12.13 via `uv` (system python is 3.10.9) | 3.12.3 ✅ |
| GPU | none | varies per pod — RTX A5000 24 GB sm_86 originally, an RTX 4000 Ada 20 GB sm_89 since |
| torch | — | 2.8.0+cu128, CUDA available ✅ |
| Present | git, node, npm, uv | git, ffmpeg, uv, flock |
| Missing | GPU | `node`, `npm`, `nvcc`, `espeak-ng` |

`node`/`npm` are absent on the pod, so **frontend work happens locally** until they are installed there.
A local `uv sync` was killed for memory (exit 137) — another reason the pod is the better build host.

`espeak-ng` is **not** needed by F5 / Chatterbox / VoxCPM — don't install it on a hunch.
Keep the pod's preinstalled **torch 2.8.0+cu128**; do not let a requirements file downgrade it to the old cu124 pins.

**VRAM budget:** `budget_mb = 16000`, `max_workers = 2` — sized for 24 GB. **Check the card before trusting
it**: pods are recreated on whatever is available, and a 20 GB RTX 4000 Ada runs VoxCPM 2 alone fine but
has far less slack for a second resident model. Read free VRAM via NVML / `mem_get_info()` — never
`total - memory_allocated()`, which sees only the current process and reports 24 GB free while another
process holds 20 GB.

---

## Commands

**Dependencies are managed with `uv`, not pip.** `uv` earns it here: the runtimes pin
`transformers`/`torchaudio` stacks that are not expected to co-resolve, and `[tool.uv] conflicts` locks them
independently instead of failing — or worse, resolving to something that satisfies the solver and breaks at
import. That is the exact class of failure that killed the predecessor (`transformers>=4.57.6` vs
fish-speech's `<=4.57.3`, unsatisfiable). `uv venv --python` per runtime is also what makes the
per-runtime-interpreter design practical.

```bash
cd backend && uv sync                     # API env — NO torch, by design
cd backend && uv run pytest -m "not gpu"  # CPU-only, ~30s — the default loop
cd backend && uv run pytest -m gpu        # pod only: real subprocess, real weights
cd frontend && npm run build      # tsc -b + vite build (no test script yet)
```

Runtime environments are separate and are NOT part of `uv sync` — one venv per worker type. On a pod,
**don't build these by hand**: `scripts/pod-bootstrap.sh` provisions all five, with the pinned
revisions and the cu128 torch pin every one of them needs (a plain install silently resolves cu130,
`torch.cuda.is_available()` goes False, and synthesis runs on CPU until it times out). The venvs and
what needs them:

| venv | Needed for | Env var |
|---|---|---|
| `.venv-voxcpm` | `voxcpm2`, `voxcpm2_urdu_arabic` — English + Roman Urdu, the default route | `VCS_VOXCPM_PYTHON` |
| `.venv-omnivoice` | `omnivoice_urdu` — Perso-Arabic Urdu, verified, picked by name | `VCS_OMNIVOICE_PYTHON` |
| `.venv-chatterbox` | `chatterbox_ml_v3` — **not routable** (failed its identity listen, Phase 4c) | `VCS_CHATTERBOX_PYTHON` |
| `.venv-qwen` | Speech Direction's LLM analyzer — *not* an audio runtime, see below | `VCS_QWEN_ANALYZER_PYTHON` |
| `.venv-gemma` | Phase B's Roman/Devanagari → Perso-Arabic transliterator — **not yet provisioned by the script** | `VCS_GEMMA_TRANSLITERATOR_PYTHON` |
| `.venv-eval` | `eval/` harness only (Whisper CER, ECAPA cosine) — never the API | — |

The Qwen analyzer is deliberately **not** a `RuntimeKind` and not in `Settings.interpreters()`: it
classifies text and must stay unreachable from `resolve()`. `AnalyzerScheduler` reads its own setting.
Forgetting `VCS_QWEN_ANALYZER_PYTHON` doesn't break generation — it breaks only the "Let AI suggest
emotion/tone" button, with a clear error naming the variable.

**The Gemma transliterator follows the same rule for the same reason**, but is shaped differently:
it converts text and must stay unreachable from `resolve()`, so it is not a `RuntimeKind` either —
yet at ~19 GB it cannot linger the way the 6 GB analyzer does. `TransliteratorScheduler` owns
nothing between calls: it takes `InferenceScheduler.exclusive_gpu()`, spawns, loads, converts, and
kills the worker, every time. Audio models come back COLD afterwards; that is the price of the
shape, not a defect. **None of it is reachable from the API yet** — see
[docs/HANDOFF.md](docs/HANDOFF.md)'s "next three things".

Run a single uvicorn worker. N workers = N schedulers = N × VRAM.

---

## Traps that have already cost time

- **Middleware order.** CORS must be added *last* (outermost), the API-key check *first*. Starlette runs
  last-added outermost, so the original order made every cross-origin preflight 403 the moment `VCS_API_KEY`
  was set.
- **`<audio>` cannot send auth headers.** Use signed media tokens (`?t=<hmac>.<exp>` → `FileResponse`, Range
  works). Don't blob-fetch: it forces a full download and breaks seeking. Same limitation bites ngrok's
  free-tier interstitial: `api.ts` adds `ngrok-skip-browser-warning` to every `fetch`, but media requests
  can't carry it, so clips can 404-to-HTML on a device that never clicked through the warning.
- **`Content-Disposition` must be `inline` for media.** `attachment` makes mobile Safari and Android Chrome
  refuse to play and offer a download instead — the play button looks dead while the download button works,
  and desktop browsers ignore the header entirely so it never shows up in local testing. The download button
  needs the server-side `&download=1` opt-in because the HTML `download` attribute is ignored cross-origin.
- **Script detection cannot tell Roman Urdu from English.** "Aap kaise hain" and "How are you" are both Latin.
  The user declares the language; the code detects the script. `(ur, LATIN)` *is* Roman Urdu.
  `dir="rtl"` keys off detected script, never `language === 'ur'`.
- **Route shadowing.** `routers/models.py` endpoints were unreachable because `settings.py` registered first.
  There's a startup assertion for duplicate `(method, path)` — keep it.
- **CSS vars.** `--fg` was used with a hardcoded fallback (`var(--fg, #f8fafc)`) and never declared in
  `variables.css` — the fallback hid the bug instead of surfacing it. `frontend/scripts/check-css-vars.mjs`
  (run via `npm run check:css-vars`, wired into `npm run build`) greps every `var(--x)` against
  `variables.css` and fails the build on a miss, with a small allowlist for genuinely dynamic
  runtime-computed properties (e.g. `--level`/`--phase` on the recording meter, written straight to
  the DOM by JS, never meant to be declared statically). `styles/variables.css` is otherwise good —
  keep it, and don't add a token there that isn't a real design token.
- **`df` does not show the `/workspace` quota.** It is a MooseFS mount and `df` reports the whole cluster —
  it will cheerfully say "164 TB free" while the volume is full. Use `du -sh /workspace` against the volume
  size in the RunPod console. This already cost an hour: a real "disk quota exceeded" was dismissed as a
  misdiagnosis because `df` looked fine. Four runtime venvs plus the uv cache reached ~49 GB; a fully
  bootstrapped pod (five venvs + all pinned weights, including Qwen2.5-3B's ~6 GB) measured **76 GB**.
  Size the volume for that, not for the older number.
- **`chatterbox-tts` needs `setuptools<81` in its own venv.** Its watermarking dependency
  (`resemble-perth`) does `from pkg_resources import resource_filename`, an API `setuptools>=81`
  removed outright. The failure is silent: `perth/__init__.py` wraps the real import in a bare
  `except ImportError: PerthImplicitWatermarker = None`, so `import perth` succeeds and the actual
  error only surfaces as an opaque `TypeError: 'NoneType' object is not callable` deep inside
  `ChatterboxMultilingualTTS.from_local()`, nowhere near `pkg_resources`. `pod-bootstrap.sh`'s
  Chatterbox section pins `setuptools<81` for exactly this reason — don't drop it.
- **Never let `torchcodec` be importable in `.venv-eval`.** `torchaudio.load()` and the transformers
  ASR pipeline both prefer TorchCodec over their older decoders when it's merely *installed* — not
  when you ask for it. Its prebuilt binaries are linked against a specific CUDA runtime
  (observed: needs `libnvrtc.so.13`) independent of whatever CUDA build of torch is actually present,
  so the failure is a hard crash (`OSError: libnvrtc.so.13: cannot open shared object file`), not a
  graceful fallback. The trap: seeing `ModuleNotFoundError: No module named 'torchcodec'` and
  installing it to fix that error makes things *worse* — transformers then prefers the broken binary
  over the working fallback it was using before. `eval/eval_harness.py` reads every audio file with
  `soundfile` (already a dependency) for exactly this reason — never `torchaudio.load()` or a bare
  file-path string into the ASR pipeline.

## Conventions

- Errors are RFC 9457 `problem+json` with a stable `code`. No `{"status":"ok","result":…}` envelope. A job's
  `status` field (queued/running/succeeded/failed/cancelled) is domain state, not a transport envelope —
  don't confuse the two when reviewing `JobStatusResponse`.
- Every route needs `response_model=`. `frontend/src/types/api.ts` is **hand-written**, mirroring
  `backend/app/api/schemas/**` — update it by hand when a schema changes. (Generating it from OpenAPI was
  the plan at one point; it was never built. Don't assume a generator exists or refuse to hand-edit this
  file — hand-editing it is correct.)
- Frontend server state is TanStack Query v5 (`frontend/src/hooks/queries.ts`), adopted 2026-08.
  **There is no Zustand** — it was never added, despite an earlier version of this file claiming
  otherwise. The API key lives in `localStorage`, read/written directly in `App.tsx`'s `ApiKeyControl`.
  Toasts and other UI-only state are plain `useState`. Don't add Zustand on the assumption it's already
  a project dependency — check `package.json` first.
- **Schema changes: add a column, nothing else.** `Database.connect()` runs an add-only pass driven by
  `_ADDED_COLUMNS` — `PRAGMA table_info`, then `ALTER TABLE … ADD COLUMN` for anything missing. Adding a
  column is one line in that tuple. It is deliberately *not* a migration framework: no renames, drops,
  type changes, backfills, or version counter, because `ADD COLUMN` is the one SQLite change that is O(1)
  and cannot lose data. Anything beyond that needs a real mechanism, which does not exist yet — don't
  extend this one into one. Older comments in `schema.sql` claiming there is no migration mechanism at
  all predate this and have been corrected; the pod's database holds the owner's real voices and history,
  so a column added only to `schema.sql` reaches a fresh install and silently misses production.
- **A newline is a paragraph break with the longest pause; a comma is a 60 ms breath.**
  `domain/direction_analyze._split_units` splits at three levels — clause (`,` and the ARABIC COMMA
  `،` U+060C, a *different codepoint* that nothing had ever handled), sentence, and paragraph.
  Newlines are split off **before** `split_sentences`, because that function calls
  `normalize_whitespace` (`" ".join(text.split())`) and destroys them. Two consequences worth
  knowing: prosody (emotion, rate) is scored on the **sentence**, not the clause — scoring a lone
  clause makes `_determine_rate`'s clause-density rule permanently unreachable — and anything that
  *builds* text (e.g. `domain/youtube.cues_to_text`) must not emit newlines it does not mean, since
  each one is now ~380 ms of real silence.
- **Never fetch a user-supplied URL.** `domain/youtube.parse_video_id` extracts an 11-character
  video id from a `urlsplit`-checked hostname and callers build their own request from that id.
  A regex over the whole URL is not sufficient — it accepts `youtube.com.evil.test` and
  `www.youtube.com@evil.test`. Tests assert not just the 422 but that **no fetch happened**.
- No `.catch(() => {})`. Ever.
