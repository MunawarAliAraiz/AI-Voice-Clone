# AI Voice Clone Studio

FastAPI + React voice-cloning studio. Target languages: **Urdu (Perso-Arabic + Roman), Hindi, English**.

Currently mid-rewrite. The authoritative design is **[docs/REWRITE_PLAN.md](docs/REWRITE_PLAN.md)** — read it before
changing anything in `backend/app/inference/`, `backend/app/domain/`, or the engine layer. This file is the
operational summary; the plan is the source of truth.

Base branch is **`dev`**, not `main`. `main` and `feature/upgrade-tts-engines` are superseded.
(`dev` does *not* contain `main`'s last 2 commits — that's known and intentional.)

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
3. **Eviction only inside `_ensure_ready()`, only while holding the GPU-slot semaphore.** This makes
   unload-during-inference unrepresentable rather than merely guarded. Don't add an eviction path elsewhere.
4. **Routing (`resolve()`) is pure.** No I/O, no `is_loaded`. Routing that consults load state is what caused
   rule 1's bug. Routing decides what *should* run; the scheduler makes it so.
5. **No silent fallback.** Unroutable request → `NoRouteError` → 422 listing what *would* work. Every response
   carries `route: {model_id, transform, lossy, rationale}` and the UI renders it as a visible chip.
6. **Permissive licenses only.** No CC-BY-NC weights, no paid tiers. Every `ModelSpec.license` must match its
   HF card. This is why XTTS v2 and Fish Speech were removed — don't reintroduce them.
7. **Pin every HuggingFace `revision`.** `trust_remote_code=True` on an unpinned `main` is a supply-chain hole
   (IndicF5 needs it).

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
| GPU | none | RTX A5000, 24 GB, sm_86 |
| torch | — | 2.8.0+cu128, CUDA available ✅ |
| Present | git, node, npm, uv | git, ffmpeg, uv, flock |
| Missing | GPU | `node`, `npm`, `nvcc`, `espeak-ng` |

`node`/`npm` are absent on the pod, so **frontend work happens locally** until they are installed there.
A local `uv sync` was killed for memory (exit 137) — another reason the pod is the better build host.

`espeak-ng` is **not** needed by F5 / Chatterbox / VoxCPM — don't install it on a hunch.
Keep the pod's preinstalled **torch 2.8.0+cu128**; do not let a requirements file downgrade it to the old cu124 pins.

**VRAM budget (24 GB):** `budget_mb = 16000`, `max_workers = 2`. Read free VRAM via NVML / `mem_get_info()` —
never `total - memory_allocated()`, which sees only the current process and reports 24 GB free while another
process holds 20 GB.

---

## Commands

**Dependencies are managed with `uv`, not pip.** `uv` earns it here: the three runtimes pin
`transformers`/`torchaudio` stacks that are not expected to co-resolve, and `[tool.uv] conflicts` locks them
independently instead of failing — or worse, resolving to something that satisfies the solver and breaks at
import. That is the exact class of failure that killed the predecessor (`transformers>=4.57.6` vs
fish-speech's `<=4.57.3`, unsatisfiable). `uv venv --python` per runtime is also what makes the
per-runtime-interpreter design practical.

```bash
cd backend && uv sync                     # API env — NO torch, by design
cd backend && uv run pytest -m "not gpu"  # CPU-only, ~30s — the default loop
cd backend && uv run pytest -m gpu        # pod only: real subprocess, real weights
cd frontend && npm run test && npm run build
```

Runtime environments are separate and are NOT part of `uv sync` — one venv per worker type:

```bash
uv venv --python 3.12 .venv-f5 && uv pip install --python .venv-f5 -e ".[f5]"
```

Run a single uvicorn worker. N workers = N schedulers = N × VRAM.

---

## Traps that have already cost time

- **Middleware order.** CORS must be added *last* (outermost), the API-key check *first*. Starlette runs
  last-added outermost, so the original order made every cross-origin preflight 403 the moment `VCS_API_KEY`
  was set.
- **`<audio>` cannot send auth headers.** Use signed media tokens (`?t=<hmac>.<exp>` → `FileResponse`, Range
  works). Don't blob-fetch: it forces a full download and breaks seeking.
- **Script detection cannot tell Roman Urdu from English.** "Aap kaise hain" and "How are you" are both Latin.
  The user declares the language; the code detects the script. `(ur, LATIN)` *is* Roman Urdu.
  `dir="rtl"` keys off detected script, never `language === 'ur'`.
- **Route shadowing.** `routers/models.py` endpoints were unreachable because `settings.py` registered first.
  There's a startup assertion for duplicate `(method, path)` — keep it.
- **CSS vars.** `--color-primary`, `--border-color`, `--transition-medium` are used but never declared. A CI
  check greps every `var(--x)` against `variables.css`. `styles/variables.css` is otherwise good — keep it.

## Conventions

- Errors are RFC 9457 `problem+json` with a stable `code`. No `{"status":"ok","result":…}` envelope.
- Every route needs `response_model=`. `frontend/src/types/api.ts` is **generated** from OpenAPI — don't hand-edit.
- Frontend server state is TanStack Query v5; Zustand holds only API key, toasts, and record device preference.
- No `.catch(() => {})`. Ever.
