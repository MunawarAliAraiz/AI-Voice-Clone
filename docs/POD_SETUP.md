# Pod setup — running the backend on a RunPod GPU

A manual runbook for standing up the backend on a fresh GPU pod, then connecting a local frontend to
it over an SSH tunnel. Assumes a fresh RunPod pod with an NVIDIA GPU (developed on an RTX A5000 /
A4500, 20–24 GB), Ubuntu, and Python 3.12.

`scripts/pod-bootstrap.sh` automates steps 0–7. **Two commands, in this order** — run both from your
laptop, in the repo root:

```bash
git archive --prefix=AI-Voice-Clone/ HEAD | ssh -p <PORT> root@<HOST> "tar -x -C /workspace"
```

```bash
ssh -p <PORT> root@<HOST> "bash -s" < scripts/pod-bootstrap.sh
```

**Why that order?** The `< scripts/pod-bootstrap.sh` redirect feeds the *script itself* over the wire
(`bash -s` reads its program from stdin), so the script never has to exist on the pod. But the work
the script does — `uv sync`, building the runtime venv — operates on the *repo*, which does have to
be there. The repo is private, so the pod cannot fetch it on its own; the archive command above puts
it in place first.

The one exception: if you supply `GH_USER` / `GH_TOKEN`, the script clones the repo itself and the
archive step is unnecessary. Prefer the archive route anyway — see step 2 for why.

Steps 0–11 below are the same sequence done by hand.

---

## Why two environments

The GPU model runs in a **separate OS process with its own interpreter** from the API. This is a
structural invariant of the project: `import torch` must not be reachable from the API process, which
keeps it CPU-only. So setup builds two virtualenvs:

- **API venv** (`backend/.venv`, via `uv sync`) — FastAPI, no torch.
- **Runtime venv** (`backend/.venv-voxcpm`) — voxcpm + torch, the process the API spawns to synthesize.

Skipping the second venv gives you a server that starts fine but whose every `/generate` fails with
*"no interpreter for runtime voxcpm"*.

---

## 0. Redirect caches off the ephemeral disk (every session)

The pod's `/` is a ~30 GB overlay that is wiped on restart and fills fast; `/workspace` is the
persistent network volume. If HuggingFace / uv / pip write to `~/.cache`, the overlay fills and kills
the pod.

```bash
export HF_HOME=/workspace/hf-cache TORCH_HOME=/workspace/torch-cache \
       PIP_CACHE_DIR=/workspace/pip-cache UV_CACHE_DIR=/workspace/uv-cache \
       TMPDIR=/workspace/tmp
mkdir -p /workspace/{hf-cache,torch-cache,pip-cache,uv-cache,tmp}
```

## 1. Ensure `uv` and `ffmpeg` are present

```bash
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="/root/.local/bin:$PATH"
command -v ffmpeg || (apt-get update -qq && apt-get install -y -qq ffmpeg)
```

## 2. Get the code onto the persistent volume

**The repo is private**, so a plain `git clone` on the pod fails without credentials.
Two routes:

**A — ship it from your machine (preferred; no token ever reaches the pod).** Run this on
your laptop, from the repo root:

```bash
git archive --prefix=AI-Voice-Clone/ HEAD | ssh -p <PORT> root@<HOST> "tar -x -C /workspace"
```

This copies the committed tree to `/workspace/AI-Voice-Clone`. There is no `.git` afterwards —
that is fine, the bootstrap script detects a pre-staged tree and skips cloning. Nothing on the pod
can push, which is the correct blast radius for a machine that gets destroyed daily.

**B — clone on the pod.** Needs a GitHub token, which then lives in `/root/.git-credentials`
(ephemeral, `chmod 600`, dies with the pod — never write it to `/workspace`):

```bash
GH_USER=<you> GH_TOKEN=<token> git clone https://github.com/MunawarAliAraiz/AI-Voice-Clone.git /workspace/AI-Voice-Clone
```

Use B only when you actually need to commit from the pod.

## 3. Backend API environment (no torch, by design)

```bash
cd /workspace/AI-Voice-Clone/backend && uv sync --python 3.12
```

## 4. VoxCPM 2 runtime environment (this one has torch)

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

## 5. Start the API, pointed at the runtime

```bash
cd /workspace/AI-Voice-Clone/backend
HF_HOME=/workspace/hf-cache \
VCS_VOXCPM_PYTHON="$PWD/.venv-voxcpm/bin/python" \
VCS_WORKER_CWD="$PWD" \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The **first** `/generate` spawns the worker and downloads the VoxCPM 2 weights (~7 GB, once, into
`/workspace/hf-cache`) then loads them — expect ~1–2 min that first time, fast thereafter. Run **one**
uvicorn worker; N workers means N schedulers, each believing it owns all the VRAM.

To keep it running after you close SSH, launch it detached:

```bash
setsid bash -c 'cd /workspace/AI-Voice-Clone/backend && \
  HF_HOME=/workspace/hf-cache \
  VCS_VOXCPM_PYTHON="$PWD/.venv-voxcpm/bin/python" \
  VCS_WORKER_CWD="$PWD" \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > /workspace/api.log 2>&1 < /dev/null &'
```

## 6. Confirm it is up (on the pod)

```bash
curl -s http://127.0.0.1:8000/api/health
```

---

## Connect a local frontend

The server binds to `127.0.0.1` on the pod (not the public internet), so reach it with an SSH tunnel.
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
`C:\Windows\System32\OpenSSH\ssh.exe` if your key lives in the Windows ssh-agent.)

---

## Public deployment (ngrok + Cloudflare)

Everything above assumes you are the only user, reaching the backend through an SSH tunnel that only
your machine can open. To share a working link with other people, two more pieces are needed: a
public, **stable** URL for the backend (SSH tunnels aren't shareable, and RunPod's own proxy URL
changes every pod restart), and a public frontend that points at it.

This deployment is: **frontend on Cloudflare Workers, backend on the pod behind an ngrok static
domain, protected by `VCS_API_KEY`.**

### One-time setup

**Backend — claim a static ngrok domain** (free tier gives one permanent domain per account, so this
survives every pod restart):

1. https://dashboard.ngrok.com/get-started/your-authtoken — copy the authtoken.
2. https://dashboard.ngrok.com/cloud-edge/domains — claim a free domain, e.g.
   `your-name.ngrok-free.dev`.

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

### Every time you (re)start the backend

On the pod:

```bash
# Re-attach ngrok's authtoken — its config lives on the ephemeral `/` overlay, wiped every pod restart
ngrok config add-authtoken <TOKEN>
tmux new-session -d -s ngrok "ngrok http --domain=<your-name>.ngrok-free.dev 8000"

# First time ever: generate secrets and save them off the ephemeral overlay
python3 -c "
import secrets
print('VCS_API_KEY=' + secrets.token_hex(32))
print('VCS_MEDIA_TOKEN_SECRET=' + secrets.token_hex(32))
" > /workspace/vcs-secrets.env

# Every restart: reuse the SAME secrets — regenerating invalidates the key
# every frontend user already has saved
set -a; source /workspace/vcs-secrets.env; set +a

cd /workspace/AI-Voice-Clone/backend
tmux new-session -d -s backend "
HF_HOME=/workspace/hf-cache \
VCS_API_KEY=\$VCS_API_KEY \
VCS_MEDIA_TOKEN_SECRET=\$VCS_MEDIA_TOKEN_SECRET \
VCS_CORS_ORIGINS='[\"https://your-frontend-url\"]' \
VCS_VOXCPM_PYTHON=/workspace/AI-Voice-Clone/backend/.venv-voxcpm/bin/python \
VCS_WORKER_CWD=/workspace/AI-Voice-Clone/backend \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 2>&1 | tee /workspace/backend.log
"
```

`/workspace/vcs-secrets.env` survives a pod stop/start (same volume-persistence rule as the venvs
below), so a routine restart is just: re-add the ngrok authtoken, re-source the secrets file, restart
both `tmux` sessions. A **new** pod (fresh `/workspace`, not just a restarted one) has no secrets
file — generate one, and treat the resulting API key as new; anyone with the old one needs to be told.

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

Then open the frontend URL, click the settings gear, paste the ngrok domain (only needed if you
didn't bake `VITE_API_BASE` in) and the `VCS_API_KEY`, and save.

**Visiting the ngrok URL itself in a browser is not the product** — it's a JSON API with no page at
`/` (that path 404s by design; `/docs` has the interactive Swagger UI if you want to poke at it
directly). The UI lives at the frontend URL, which calls the ngrok URL under the hood.

---

## Notes

- **Persistence.** Everything under `/workspace` (both venvs, the weights cache, and `backend/data/`
  with your voices + history) survives a pod **stop/start** — but not a pod **delete**. RunPod assigns
  a **new host:port** on restart, so the tunnel command changes each time.
- **API key.** Not needed for the localhost-tunnel setup: leave `VCS_API_KEY` unset and the UI's
  API-key box blank. Set it only if you expose the port publicly (RunPod HTTP proxy or binding
  `0.0.0.0`) instead of tunneling — then the same string goes on the backend (`VCS_API_KEY`) and in the
  UI. Note `VCS_CORS_ORIGINS="*"` together with a key set is refused at startup.
- **Native Urdu script (اردو).** Not routable in the default deployment — the app returns a 422
  rather than mis-rendering it. Use Roman Urdu ("aap kaise hain"). See the README for why.
