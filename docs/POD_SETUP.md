# Pod setup — running the backend on a RunPod GPU

Assumes a fresh RunPod pod with an NVIDIA GPU (developed on an RTX A5000 / A4500, 20–24 GB), Ubuntu,
and Python 3.12.

## The one command

The repo is public for read — cloning needs no token. From your laptop:

```bash
ssh root@<HOST> -p <PORT> "bash -s" < scripts/pod-bootstrap.sh
```

That's it for a fresh pod. It takes a few minutes (mostly downloading the VoxCPM 2 weights, ~7 GB) and
ends by printing the exact command to start serving, including a freshly generated `VCS_API_KEY` and
`VCS_MEDIA_TOKEN_SECRET`. Copy that command, run it, and the backend is up.

You do **not** need `GH_USER` / `GH_TOKEN` for this — those are only for pushing commits *from* the
pod, which most sessions never do. If you do need to push from the pod, add them:

```bash
ssh root@<HOST> -p <PORT> "GH_USER=<you> GH_TOKEN=<token> bash -s" < scripts/pod-bootstrap.sh
```

**What that command actually does**, so you know what to expect and where to look if something fails:

- Redirects HuggingFace/torch/pip/uv caches onto `/workspace` — the pod's `/` is a ~30 GB overlay that
  fills and kills the pod otherwise.
- Clones the repo to `/workspace/AI-Voice-Clone` (or pulls, if it's already there from a previous
  session on this same pod).
- Installs `uv` and `ffmpeg` if missing.
- Builds two separate Python environments — this is a structural requirement of the project, not
  incidental: `import torch` must never be reachable from the API process, so the GPU model runs in a
  **separate interpreter** the API talks to over a wire protocol. `uv sync` builds the API's env (no
  torch); a second `uv venv` builds the VoxCPM 2 runtime env, pinned to the **cu128** torch build —
  a plain `pip install voxcpm` pulls a cu130 wheel whose CUDA runtime is newer than the RunPod driver,
  which makes `torch.cuda.is_available()` silently `False` and every generation quietly falls back to
  CPU and times out.
- Downloads the VoxCPM 2 weights (~7 GB, pinned revision) into the HF cache on `/workspace`.
- Runs the CPU test suite as a sanity check.
- If `NGROK_AUTHTOKEN` is set, installs and configures ngrok too (see [Public deployment](#public-deployment-ngrok--cloudflare) below).

Re-running the same command on the same pod is safe — every step checks whether its work already
exists before doing it again.

---

## Start serving

The bootstrap script prints this at the end, with real values filled in:

```bash
cd /workspace/AI-Voice-Clone/backend
HF_HOME=/workspace/hf-cache \
VCS_API_KEY=<generated> \
VCS_MEDIA_TOKEN_SECRET=<generated> \
VCS_CORS_ORIGINS='["https://your-frontend-url"]' \
VCS_WARM_ON_STARTUP=voxcpm2 \
VCS_VOXCPM_PYTHON=/workspace/AI-Voice-Clone/backend/.venv-voxcpm/bin/python \
VCS_WORKER_CWD=/workspace/AI-Voice-Clone/backend \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`VCS_WARM_ON_STARTUP=voxcpm2` starts loading the model in the background the moment the process boots,
instead of leaving the first real `/generate` to pay the ~20–60 s cold-load cost. `/api/health`
answers immediately either way — the model just happens to already be resident by the time someone
uses the app, rather than making the first request wait.

Run **one** uvicorn worker. N workers means N schedulers, each believing it owns the whole VRAM budget.

To keep it running after you close the SSH session, launch it detached — either `tmux` (below, also
used for ngrok) or `setsid`:

```bash
tmux new-session -d -s backend "cd /workspace/AI-Voice-Clone/backend && \
  HF_HOME=/workspace/hf-cache VCS_API_KEY=<...> VCS_MEDIA_TOKEN_SECRET=<...> \
  VCS_CORS_ORIGINS='[\"https://your-frontend-url\"]' VCS_WARM_ON_STARTUP=voxcpm2 \
  VCS_VOXCPM_PYTHON=/workspace/AI-Voice-Clone/backend/.venv-voxcpm/bin/python \
  VCS_WORKER_CWD=/workspace/AI-Voice-Clone/backend \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 2>&1 | tee /workspace/backend.log"
```

Confirm it's up, on the pod:

```bash
curl -s http://127.0.0.1:8000/api/health
```

---

## Resuming after a pod restart

A pod **stop/start** keeps `/workspace` (both venvs, the weights cache, `backend/data/` with your
voices + history) but wipes `/`, and kills every running process. So the code and weights are still
there — you just need to restart the backend (and ngrok, if you're using it):

```bash
cd /workspace/AI-Voice-Clone/backend
# reuse the SAME VCS_API_KEY / VCS_MEDIA_TOKEN_SECRET you generated the first time —
# see "Public deployment" below for why regenerating them breaks things
HF_HOME=/workspace/hf-cache \
VCS_API_KEY=<same as before> \
VCS_MEDIA_TOKEN_SECRET=<same as before> \
VCS_CORS_ORIGINS='["https://your-frontend-url"]' \
VCS_WARM_ON_STARTUP=voxcpm2 \
VCS_VOXCPM_PYTHON=/workspace/AI-Voice-Clone/backend/.venv-voxcpm/bin/python \
VCS_WORKER_CWD=/workspace/AI-Voice-Clone/backend \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

You do not need to re-run the full bootstrap script — nothing it built was lost. Re-running it anyway
is harmless (every step is a no-op if its work already exists) if you're not sure what state the pod
is in.

A **new** pod (fresh `/workspace`, not just a restart) has none of this — that's when you run the full
bootstrap command from the top of this doc again.

---

## Connect a local frontend (SSH tunnel)

For reaching the backend yourself, from your own machine — not for sharing a link with anyone else.
The server binds to `127.0.0.1` on the pod, not the public internet, so reach it with an SSH tunnel.
Run this **on your own machine** and leave it open:

```bash
ssh -N -L 8000:127.0.0.1:8000 -p <POD_PORT> root@<POD_HOST>
```

Then start the UI locally — it defaults to `http://localhost:8000`, which the tunnel forwards to the
pod:

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:1420 — the status chip should read **backend online**. (On Windows, use
`C:\Windows\System32\OpenSSH\ssh.exe` if your key lives in the Windows ssh-agent — Git Bash's `ssh`
can't see it.)

No `VCS_API_KEY` needed for this path — leave it unset on the backend and the UI's API-key box blank.
RunPod assigns a new host:port on every restart, so the tunnel command changes each time.

---

## Public deployment (ngrok + Cloudflare)

For sharing a working link with other people, not just reaching the backend yourself. Two more pieces
are needed beyond the tunnel above: a public, **stable** URL for the backend (SSH tunnels aren't
shareable, and RunPod's own proxy URL changes every pod restart), and a public frontend that points at
it.

This deployment is: **frontend on Cloudflare Workers, backend on the pod behind an ngrok static
domain, protected by `VCS_API_KEY`.**

### One-time setup

**Backend — claim a static ngrok domain** (free tier gives one permanent domain per account, so this
survives every pod restart):

1. https://dashboard.ngrok.com/get-started/your-authtoken — copy the authtoken.
2. https://dashboard.ngrok.com/cloud-edge/domains — claim a free domain, e.g.
   `your-name.ngrok-free.dev`.

Pass `NGROK_AUTHTOKEN` (and optionally `NGROK_DOMAIN`) to the bootstrap script and it installs and
configures ngrok as part of the same run:

```bash
ssh root@<HOST> -p <PORT> "NGROK_AUTHTOKEN=<token> NGROK_DOMAIN=<your-name>.ngrok-free.dev bash -s" \
  < scripts/pod-bootstrap.sh
```

**Frontend — deploy `frontend/` to Cloudflare.** The dashboard offers two different products under
similar names — check which fields you're shown:

- **Classic Pages** (Build command / Output directory / Root directory): output directory
  `frontend/dist`, root directory `frontend`, build command `npm install && npm run build`.
- **Workers Builds** (Build command / Deploy command / Path) — what this project is actually set up
  for, via `frontend/wrangler.toml`:
  - Path: `frontend`
  - Build command: `npm install && npm run build`
  - Deploy command: `npx wrangler deploy`

Either way, set a **build-time** environment variable `VITE_API_BASE` to the ngrok domain from step
above (e.g. `https://your-name.ngrok-free.dev`) — Vite inlines it into the JS bundle at build time, so
it must be present *before* `npm run build` runs, not just at deploy.

Do **not** add `frontend/public/_redirects` back if you're on Workers Builds — `wrangler.toml`'s
`not_found_handling = "single-page-application"` already does the SPA fallback, and having both
triggers Cloudflare's "infinite redirect loop" validator (error code 100324).

### Starting ngrok

If you passed `NGROK_AUTHTOKEN` to the bootstrap script, ngrok is installed and its authtoken is
configured — but its config lives on the ephemeral `/` overlay, so it needs re-adding after every pod
restart even though ngrok itself doesn't need reinstalling:

```bash
ngrok config add-authtoken <TOKEN>   # only needed again after a pod restart
tmux new-session -d -s ngrok "ngrok http --domain=<your-name>.ngrok-free.dev 8000"
```

### Generating and reusing secrets

The bootstrap script's printed serve command already includes freshly generated `VCS_API_KEY` and
`VCS_MEDIA_TOKEN_SECRET` values the **first** time you run it. Save them somewhere durable (password
manager, not just the terminal scrollback) — then reuse the *same* values on every subsequent restart:

```bash
python3 -c "
import secrets
print('VCS_API_KEY=' + secrets.token_hex(32))
print('VCS_MEDIA_TOKEN_SECRET=' + secrets.token_hex(32))
" > /workspace/vcs-secrets.env   # do this ONCE; /workspace survives restarts

set -a; source /workspace/vcs-secrets.env; set +a   # do this on every restart
```

Regenerating instead of reusing invalidates the API key every frontend user already has saved in their
browser — they'll all start getting 401s with no visible cause. A **new** pod (fresh `/workspace`) has
no secrets file and needs a fresh one; treat the resulting key as new and tell anyone who had the old
one.

### Verifying it

```bash
# Backend reachable and healthy (the ngrok header bypasses the free-tier interstitial —
# without it you'll see an HTML warning page instead of JSON)
curl -H "ngrok-skip-browser-warning: true" https://your-name.ngrok-free.dev/api/health

# CORS preflight succeeds for the deployed frontend's exact origin
curl -X OPTIONS -H "Origin: https://your-frontend-url" \
  -H "Access-Control-Request-Method: GET" \
  -H "ngrok-skip-browser-warning: true" \
  https://your-name.ngrok-free.dev/api/models
```

Then open the frontend URL, click the settings gear, paste the ngrok domain (only needed if you didn't
bake `VITE_API_BASE` in) and the `VCS_API_KEY`, and save.

**Visiting the ngrok URL itself in a browser is not the product** — it's a JSON API with no page at
`/` (that path 404s by design; `/docs` has the interactive Swagger UI if you want to poke at it
directly). The UI lives at the frontend URL, which calls the ngrok URL under the hood.

---

## Doing it by hand

The bootstrap script automates everything above. This section is a reference for when something in it
fails and you need to run the individual pieces yourself to see where — not a set of steps to follow
alongside the script under normal use.

**Caches**, so HF/torch/pip/uv don't fill the ephemeral `/` overlay:

```bash
export HF_HOME=/workspace/hf-cache TORCH_HOME=/workspace/torch-cache \
       PIP_CACHE_DIR=/workspace/pip-cache UV_CACHE_DIR=/workspace/uv-cache \
       TMPDIR=/workspace/tmp
mkdir -p /workspace/{hf-cache,torch-cache,pip-cache,uv-cache,tmp}
```

**`uv` and `ffmpeg`:**

```bash
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="/root/.local/bin:$PATH"
command -v ffmpeg || (apt-get update -qq && apt-get install -y -qq ffmpeg)
```

**The repo** — public for read, no token needed:

```bash
git clone https://github.com/MunawarAliAraiz/AI-Voice-Clone.git /workspace/AI-Voice-Clone
```

If you need to push *from* the pod (rare — normally you develop locally and the pod only runs), a
token is needed for that direction only:

```bash
GH_USER=<you> GH_TOKEN=<token> git clone https://github.com/MunawarAliAraiz/AI-Voice-Clone.git /workspace/AI-Voice-Clone
```

**Backend API environment** (no torch, by design):

```bash
cd /workspace/AI-Voice-Clone/backend && uv sync --python 3.12
```

**VoxCPM 2 runtime environment** (this one has torch):

```bash
uv venv .venv-voxcpm --python 3.12
uv pip install --python .venv-voxcpm voxcpm
```

> **Critical torch pin.** A plain `voxcpm` install pulls a **torch cu130** wheel whose CUDA runtime is
> newer than the RunPod driver, so `torch.cuda.is_available()` is silently `False`, synthesis falls to
> CPU, and the request times out. Force the cu128 build the driver supports:

```bash
uv pip install --python .venv-voxcpm \
  torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
```

Verify — this **must** print `True`:

```bash
.venv-voxcpm/bin/python -c "import torch; print('cuda available:', torch.cuda.is_available())"
```

If your pod's driver is newer than CUDA 12.8, check `nvidia-smi` and bump the index URL (`cu129` /
`cu130`) to match the driver.

**VoxCPM 2 weights** (pinned revision, ~7 GB, cached on `/workspace`):

```bash
python3 - <<'PY'
from huggingface_hub import snapshot_download
p = snapshot_download("openbmb/VoxCPM2", revision="bffb3df5a29440629464e5e839f4d214c8714c3d")
print("weights at", p)
PY
```

---

## Notes

- **Native Urdu script (اردو).** Not routable in the default deployment — the app returns a 422
  rather than mis-rendering it. Use Roman Urdu ("aap kaise hain"). See the README for why.
