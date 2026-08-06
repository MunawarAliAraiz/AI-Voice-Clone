# Pod setup — running the backend on a RunPod GPU

A manual runbook for standing up the backend on a fresh GPU pod, then connecting a local frontend to
it over an SSH tunnel. This is the same sequence `scripts/pod-bootstrap.sh` automates — run that if
you want it in one command:

```bash
ssh root@<HOST> -p <PORT> "GH_USER=you GH_TOKEN=ghp_… bash -s" < scripts/pod-bootstrap.sh
```

Otherwise, the steps below do it by hand. They assume a fresh RunPod pod with an NVIDIA GPU (developed
on an RTX A5000 / A4500, 20–24 GB), Ubuntu, and Python 3.12.

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

## 2. Clone the repo onto the persistent volume

```bash
git clone https://github.com/IftikharAhmedDev/AI-Voice-Clone.git /workspace/AI-Voice-Clone
cd /workspace/AI-Voice-Clone && git checkout rewrite/contracts
```

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
