# AI Voice Clone Studio

Self-hosted zero-shot voice cloning and text-to-speech for **Urdu**, **Hindi**, and **English**.
Web app — FastAPI backend, React frontend, open models running on your own GPU. No API keys, no
external services, no per-character billing.

> **Status: mid-rewrite.** The interface contracts are frozen; the engine layer is being rebuilt.
> Design and rationale: [docs/REWRITE_PLAN.md](docs/REWRITE_PLAN.md).
> Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
> Working agreements for contributors and agents: [CLAUDE.md](CLAUDE.md).
>
> Base branch is `dev`. `main` and `feature/upgrade-tts-engines` are superseded.

## Languages

Urdu is the hard case: no permissively-licensed model does Urdu zero-shot cloning well. So it ships
two ways, and the app tells you which one it used.

| You write | Script | How it is rendered |
|---|---|---|
| اردو | Perso-Arabic | Native Urdu checkpoint |
| Roman Urdu (*"aap kaise hain"*) | Latin | Transliterated to Devanagari → Hindi model |
| हिन्दी | Devanagari | Hindi model |
| English | Latin | English model |

**You declare the language; the app detects the script.** It cannot guess Roman Urdu from English —
"Aap kaise hain" and "How are you" are both Latin — so it does not try. Every result carries a
visible chip naming the model and any transformation applied, including whether it was lossy. You
should never have to wonder why your Urdu came out sounding like Hindi.

If no model can render what you asked for, you get a 422 listing what *would* work — never
substituted audio.

## Models

Three runtimes, five checkpoints, **permissive licenses only**.

| Runtime | Checkpoints | License |
|---|---|---|
| F5 | OpenBible Urdu · IndicF5 · OpenF5 English | CC-BY-SA-4.0 · MIT · Apache-2.0 |
| Chatterbox | Multilingual v3 | MIT |
| VoxCPM | VoxCPM 2 | Apache-2.0 |

XTTS v2 (CPML) and Fish Speech (research license) were removed: both are non-commercial. So was
ChatTTS — it never used the reference audio at all, so it was not cloning anything.

Model capability claims are not taken from README files. Each (model × language × script) pair must
pass a measured gate — CER < 25 %, speaker similarity > 0.70, faster than realtime — or it is
removed from the catalog rather than advertised.

## Requirements

- **NVIDIA GPU, 12 GB+ VRAM** (developed on a 24 GB RTX A5000). CPU inference is not supported.
- **Python 3.12** and [`uv`](https://docs.astral.sh/uv/)
- **Node 20+**
- **ffmpeg** on `PATH`

## Quick start

```bash
git clone https://github.com/IftikharAhmedDev/AI-Voice-Clone.git
cd AI-Voice-Clone
```

Backend — the API environment deliberately contains **no torch**; model runtimes live in separate
processes with their own interpreters:

```bash
cd backend && uv sync && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd frontend && npm install && npm run dev
```

Run **one** uvicorn worker. N workers means N schedulers, each believing it owns all your VRAM.

## Configuration

Environment variables, prefix `VCS_`, from `backend/.env`:

| Variable | Default | Notes |
|---|---|---|
| `VCS_HOST` / `VCS_PORT` | `0.0.0.0` / `8000` | |
| `VCS_API_KEY` | *(empty)* | **Empty means no authentication.** Set it before exposing the port. |
| `VCS_CORS_ORIGINS` | *(empty)* | Explicit origin list. `*` with an API key set is refused at boot. |
| `VCS_DATA_DIR` | `./data` | Voices, generations, database |
| `VCS_USE_GPU` / `VCS_GPU_DEVICE` | `true` / `cuda:0` | |

Point `HF_HOME` at persistent storage or you will re-download several GB after every restart.

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
