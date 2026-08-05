# AI Voice Clone Studio

Self-hosted zero-shot voice cloning and text-to-speech for **Urdu**, **Hindi**, and **English**.
Web app — FastAPI backend, React frontend, open models running on your own GPU. No API keys, no
external services, no per-character billing.

> **Status:** the rewrite is functionally complete and validated end-to-end on GPU with real cloned
> audio (VoxCPM 2). The F5 and Chatterbox runtimes remain in the catalog but are not yet wired into
> the standard deployment.
> Design and rationale: [docs/REWRITE_PLAN.md](docs/REWRITE_PLAN.md).
> Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
> Working agreements for contributors and agents: [CLAUDE.md](CLAUDE.md).
>
> Active branch is `rewrite/contracts`. `main` and `feature/upgrade-tts-engines` are superseded.

## Languages

**You declare the language; the app detects the script.** It cannot guess Roman Urdu from English —
"Aap kaise hain" and "How are you" are both Latin — so it does not try. Every result carries a
visible chip naming the model and any transformation applied, including whether it was lossy.

The default deployment runs **VoxCPM 2**, which is tokenizer-free and renders romanized text
directly — so Roman Urdu and Hinglish need **no transliteration step** (verified by ear, 2026-08):

| You write | Script | Renders as |
|---|---|---|
| Roman Urdu (*"aap kaise hain"*) | Latin | VoxCPM 2, text passed through unchanged |
| Hinglish / Roman Hindi (*"aaj main bahar ja raha hoon"*) | Latin | VoxCPM 2, unchanged |
| English | Latin | VoxCPM 2, unchanged |
| हिन्दी | Devanagari | VoxCPM 2, unchanged |
| اردو | Perso-Arabic | **No route yet** — see below |

Cloning is **cross-lingual**: the reference speaker need not have spoken the target language.

**Native Urdu script (اردو) is not routable in the default deployment.** The only catalog cell that
claims Perso-Arabic Urdu is an F5 Bible-domain checkpoint that is unverified and not deployed, so the
request is *refused* (422) rather than mis-rendered. Use Roman Urdu today. VoxCPM 2 can almost
certainly read Perso-Arabic directly — enabling it is a one-clip listening verification away, per the
"never route on an unmeasured claim" rule.

If no model can render what you asked for, you get a 422 listing what *would* work — never
substituted audio.

## Models

Three runtimes, four checkpoints, **permissive licenses only**. VoxCPM 2 is the deployed default;
the F5 and Chatterbox runtimes are in the catalog but not wired up in the standard setup below.

| Runtime | Checkpoints | License | Status |
|---|---|---|---|
| VoxCPM | VoxCPM 2 | Apache-2.0 | **deployed default** — validated end-to-end on GPU |
| Chatterbox | Multilingual v3 | MIT | catalog only |
| F5 | OpenBible Urdu · IndicF5 | CC-BY-SA-4.0 · MIT | catalog only |

XTTS v2 (CPML) and Fish Speech (research license) were removed: both are non-commercial. So was
ChatTTS — it never used the reference audio at all, so it was not cloning anything. OpenF5 English
was dropped too: every permissively-*tagged* English F5 checkpoint traces back to CC-BY-NC weights.

Model capability claims are not taken from README files. Each (model × language × script) pair must
pass a measured gate — CER < 25 %, speaker similarity > 0.70, faster than realtime — or it is
removed from the catalog rather than advertised.

## Requirements

- **NVIDIA GPU, 12 GB+ VRAM** (developed on a 24 GB RTX A5000). CPU inference is not supported.
- **Python 3.12** and [`uv`](https://docs.astral.sh/uv/)
- **Node 20+**
- **ffmpeg** on `PATH`

## Quick start

The design puts the GPU model in a **separate OS process with its own interpreter**, so setup has two
environments: the API venv (no torch, by design) and one runtime venv per model. Getting audio out
requires both — starting only the API gives you a server whose every `/generate` fails with "no
interpreter for runtime voxcpm".

```bash
git clone https://github.com/IftikharAhmedDev/AI-Voice-Clone.git
cd AI-Voice-Clone
```

**1. API environment** — deliberately contains **no torch**:

```bash
cd backend && uv sync
```

**2. VoxCPM 2 runtime environment** — a second venv that *does* have torch. On a fresh GPU box,
`scripts/pod-bootstrap.sh` does all of this for you (see [Cloud / RunPod](#cloud--runpod) below). By
hand:

```bash
uv venv backend/.venv-voxcpm --python 3.12
uv pip install --python backend/.venv-voxcpm voxcpm
# Pin torch to the CUDA build your driver supports. A plain `pip install voxcpm`
# pulls a cu130 wheel that silently reports cuda.is_available()==False on older
# drivers, then runs on CPU and times out. Check your driver's max CUDA with
# `nvidia-smi`; cu128 fits driver 525+ / CUDA 12.8:
uv pip install --python backend/.venv-voxcpm \
  torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
# verify — this MUST print True:
backend/.venv-voxcpm/bin/python -c "import torch; print(torch.cuda.is_available())"
```

**3. Start the API, pointed at that runtime:**

```bash
cd backend
export HF_HOME=/path/to/persistent/hf-cache          # or several GB re-download every restart
export VCS_VOXCPM_PYTHON="$PWD/.venv-voxcpm/bin/python"
export VCS_WORKER_CWD="$PWD"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The first `/generate` of a process spawns the worker and downloads/loads the weights (~7 GB, once);
after that it is roughly real-time. Run **one** uvicorn worker — N workers means N schedulers, each
believing it owns all your VRAM.

**4. Frontend:**

```bash
cd frontend && npm install && npm run dev      # http://localhost:1420
```

Set `VITE_API_BASE` if the API is not at `http://localhost:8000`.

### No-GPU smoke test

To exercise the full stack on a machine with no GPU, enable the gated silence runtime — it returns
silent audio with a loud `X-Fake-Audio: true` marker, never a fallback masquerading as real output:

```bash
cd backend && VCS_ALLOW_FAKE_RUNTIME=1 uv run uvicorn app.main:app --port 8000
```

## Cloud / RunPod

`scripts/pod-bootstrap.sh` rebuilds a fresh GPU pod from zero in one command — caches redirected off
the ephemeral overlay, repo, API venv, **and the VoxCPM 2 runtime venv with the cu128 torch pin and
weights**. Pipe it in over SSH:

```bash
ssh root@HOST -p PORT "GH_USER=you GH_TOKEN=ghp_… bash -s" < scripts/pod-bootstrap.sh
```

It prints the exact `uvicorn` command (with `VCS_VOXCPM_PYTHON` / `VCS_WORKER_CWD` set) to start
serving. To reach the API from your laptop, forward the port over SSH rather than exposing it:
`ssh -N -L 8000:127.0.0.1:8000 -p PORT root@HOST`.

## Configuration

Environment variables, prefix `VCS_`, from `backend/.env` (host/port are `uvicorn` CLI flags, not
settings):

| Variable | Default | Notes |
|---|---|---|
| `VCS_VOXCPM_PYTHON` | *(empty)* | **Required to generate.** Absolute path to the runtime venv's python. Empty = VoxCPM is unrunnable and `/generate` 422s. |
| `VCS_WORKER_CWD` | *(process cwd)* | Directory workers start in; must contain the importable `app` package (i.e. `backend/`). |
| `VCS_API_KEY` | *(empty)* | **Empty means no authentication.** Set it before exposing the port. |
| `VCS_CORS_ORIGINS` | Vite dev ports | JSON array of allowed origins. |
| `VCS_ALLOW_FAKE_RUNTIME` | `false` | Enables the silence runtime for GPU-less testing. |
| `VCS_DATA_DIR` | `./data` | Voices, generations, database. |
| `VCS_MEDIA_TOKEN_SECRET` | *(random per boot)* | Set in production so signed media URLs survive restarts. |
| `VCS_BUDGET_MB` / `VCS_MAX_WORKERS` | `16000` / `2` | Scheduler capacity; defaults suit a 24 GB card. |
| `VCS_CHATTERBOX_PYTHON` / `VCS_F5_PYTHON` | *(empty)* | Runtime venvs for the other engines, when wired up. |

There is no `default_engine` setting. Routing decides per request from the declared language and the
detected script.

## Tests

```bash
cd backend && uv run pytest              # CPU-only, no torch, ~30s
cd backend && uv run pytest -m gpu       # real weights, real GPU
cd frontend && npm run test && npm run build
```

The scheduler tests — including the ones covering concurrent load and eviction — need no GPU.

## Licensing and consent

The code is one thing; the **weights** are another. Every model here is permissively licensed, but
CC-BY-SA-4.0 (OpenBible Urdu) requires attribution — see `NOTICE`.

Only clone voices that are your own or that you have the speaker's explicit consent to use. Voice
recordings are biometric data in a number of jurisdictions.
