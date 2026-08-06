#!/usr/bin/env bash
# Bootstrap a FRESH GPU pod for this project, from zero.
#
# Pods are recreated often and their `/` is a 30GB ephemeral overlay that is
# wiped every time. This script rebuilds everything that is not in git, so a new
# pod is productive in one command instead of an hour of rediscovery.
#
# The repo is PUBLIC for read (clone/fetch need no credentials). Just run:
#
#   ssh root@HOST -p PORT "bash -s" < scripts/pod-bootstrap.sh
#
# GH_USER / GH_TOKEN are only needed if you plan to `git push` FROM the pod —
# without them, `git pull` still works, only pushing prompts and fails
# non-interactively. If you do set them, the token is written only to /root
# (ephemeral, chmod 600) — NEVER to /workspace, which persists and is snapshotted:
#
#   ssh root@HOST -p PORT "GH_USER=u GH_TOKEN=t bash -s" < scripts/pod-bootstrap.sh

set -euo pipefail

REPO_URL="https://github.com/MunawarAliAraiz/AI-Voice-Clone.git"
REPO_DIR="/workspace/AI-Voice-Clone"
# main now carries the rewrite (merged 2026-08-06). Override with BRANCH=… if
# you need to bootstrap a pod against a feature branch.
BRANCH="${BRANCH:-main}"

echo "== 1. caches off the ephemeral overlay =="
# The 30GB overlay fills and kills the pod if HF/uv/pip write to ~/.cache.
mkdir -p /workspace/{hf-cache,torch-cache,pip-cache,uv-cache,tmp}
cat > /workspace/engines-lab-ENV.sh <<'ENVEOF'
export HF_HOME=/workspace/hf-cache
export TORCH_HOME=/workspace/torch-cache
export PIP_CACHE_DIR=/workspace/pip-cache
export UV_CACHE_DIR=/workspace/uv-cache
export TMPDIR=/workspace/tmp
ENVEOF
grep -q 'HF_HOME' /root/.bashrc 2>/dev/null || cat /workspace/engines-lab-ENV.sh >> /root/.bashrc
# shellcheck disable=SC1091
source /workspace/engines-lab-ENV.sh

echo "== 2. git identity + credentials (ephemeral /root only) =="
git config --global user.name  "${GIT_NAME:-MunawarAliAraiz}"
git config --global user.email "${GIT_EMAIL:-mnrkokhar@gmail.com}"
if [ -n "${GH_TOKEN:-}" ] && [ -n "${GH_USER:-}" ]; then
  umask 077
  printf 'https://%s:%s@github.com\n' "$GH_USER" "$GH_TOKEN" > /root/.git-credentials
  chmod 600 /root/.git-credentials
  git config --global credential.helper store
  echo "   credentials written to /root/.git-credentials (dies with the pod, by design)"
else
  echo "   WARNING: GH_USER/GH_TOKEN not set — pushes will prompt and fail non-interactively"
fi

echo "== 3. repo =="
# The repo is public for read, so a plain clone needs no credentials. Three
# cases:
#   a) a real checkout is here      -> fetch + checkout as usual
#   b) files are here but no .git   -> a tree was staged some other way (e.g.
#                                      `git archive` from an uncommitted local
#                                      branch you want to test). Use it as-is.
#   c) nothing is here              -> clone (no token required)
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" remote set-url origin "$REPO_URL"
  git -C "$REPO_DIR" fetch origin --prune
  git -C "$REPO_DIR" checkout "$BRANCH" 2>/dev/null \
    || git -C "$REPO_DIR" checkout -b "$BRANCH" --track "origin/$BRANCH"
  git -C "$REPO_DIR" pull --ff-only origin "$BRANCH" || true
elif [ -f "$REPO_DIR/backend/pyproject.toml" ]; then
  echo "   using the pre-staged tree at $REPO_DIR (no .git)"
else
  git clone "$REPO_URL" "$REPO_DIR"
  git -C "$REPO_DIR" checkout "$BRANCH" 2>/dev/null \
    || git -C "$REPO_DIR" checkout -b "$BRANCH" --track "origin/$BRANCH"
fi

echo "== 4. system deps =="
command -v ffmpeg >/dev/null || { apt-get update -qq && apt-get install -y -qq ffmpeg; }
command -v flock  >/dev/null || { apt-get update -qq && apt-get install -y -qq util-linux; }
command -v uv     >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

echo "== 5. backend API env (NO torch — that is the structural invariant) =="
cd "$REPO_DIR/backend" && uv sync --python 3.12

echo "== 6. VoxCPM 2 runtime env (the process that ACTUALLY has torch) =="
# The GPU model runs in a separate interpreter from the API. This is the env the
# API points VCS_VOXCPM_PYTHON at; without it, every /generate 422s.
VOX_VENV="$REPO_DIR/backend/.venv-voxcpm"
if [ ! -x "$VOX_VENV/bin/python" ]; then
  uv venv "$VOX_VENV" --python 3.12
fi
uv pip install --python "$VOX_VENV" voxcpm 2>&1 | tail -2
# CRITICAL: a plain voxcpm install pulls a torch cu130 wheel whose CUDA runtime
# is newer than most drivers, so torch.cuda.is_available() is silently False and
# synthesis runs on CPU and times out. Pin the cu128 build the RunPod drivers
# support. If your driver is newer, bump the index url to match.
uv pip install --python "$VOX_VENV" \
  torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 2>&1 | tail -2
echo "   torch CUDA visible:"
"$VOX_VENV/bin/python" -c "import torch; print('   ->', torch.__version__, 'cuda', torch.cuda.is_available())"

echo "== 7. VoxCPM 2 weights (pinned revision, ~7GB, cached on /workspace) =="
VOX_REV="bffb3df5a29440629464e5e839f4d214c8714c3d"
"$VOX_VENV/bin/python" - "$VOX_REV" <<'PY' 2>&1 | tail -2
import sys
from huggingface_hub import snapshot_download
p = snapshot_download("openbmb/VoxCPM2", revision=sys.argv[1])
print("   weights at", p)
PY

echo "== 8. research lab =="
mkdir -p /workspace/engines-lab/{r1-f5,r2-chatterbox,r3-voxcpm,r4-urdu}
cp /workspace/engines-lab-ENV.sh /workspace/engines-lab/ENV.sh
touch /workspace/engines-lab/.gpu.lock   # serializes GPU access between agents

echo "== 9. verify =="
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
python3 --version
cd "$REPO_DIR/backend" && uv run pytest -q -m "not gpu" 2>&1 | tail -3
df -h / /workspace | tail -2

echo "== 10. ngrok (OPTIONAL — only if NGROK_AUTHTOKEN is set) =="
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  if ! command -v ngrok >/dev/null; then
    echo "   installing ngrok..."
    curl -sSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz | tar -xz -C /usr/local/bin
  fi

  ngrok config add-authtoken "$NGROK_AUTHTOKEN"
  if [ -n "${NGROK_DOMAIN:-}" ]; then
    echo "   ngrok will use static domain: $NGROK_DOMAIN"
  else
    echo "   WARNING: NGROK_DOMAIN not set — ngrok will use random URL"
  fi

  echo "   ngrok configured. Start it with:"
  echo "     nohup ngrok http --domain=${NGROK_DOMAIN} 8000 > /workspace/ngrok.log 2>&1 &"
else
  echo "   skipped (NGROK_AUTHTOKEN not set)"
fi

echo
echo "== READY =="
echo "  repo:    $REPO_DIR ($BRANCH)"
echo "  runtime: $VOX_VENV  (torch cu128)"
echo "  lab:     /workspace/engines-lab/"
echo "  caches:  /workspace/{hf,torch,pip,uv}-cache"
echo
echo "Start serving:"
echo "  cd $REPO_DIR/backend && \\"
echo "    HF_HOME=/workspace/hf-cache \\"
echo "    VCS_API_KEY=\$(python -c 'import secrets; print(secrets.token_hex(32))') \\"
echo "    VCS_MEDIA_TOKEN_SECRET=\$(python -c 'import secrets; print(secrets.token_hex(32))') \\"
echo "    VCS_CORS_ORIGINS='[\"https://YOUR-PAGES-URL.pages.dev\"]' \\"
echo "    VCS_WARM_ON_STARTUP=voxcpm2 \\"
echo "    VCS_VOXCPM_PYTHON=$VOX_VENV/bin/python \\"
echo "    VCS_WORKER_CWD=$REPO_DIR/backend \\"
echo "    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  echo "Then start ngrok in another session:"
  echo "  ngrok http --domain=${NGROK_DOMAIN} 8000"
  echo
fi
echo "Reach it from your laptop with SSH tunnel (alternative to ngrok):"
echo "  ssh -N -L 8000:127.0.0.1:8000 -p <POD_PORT> root@<POD_HOST>"
