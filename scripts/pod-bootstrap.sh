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

echo "== 8. secrets (generated once, reused on every restart) =="
# These MUST be stable across restarts. Regenerating VCS_API_KEY 401s every
# frontend that has the old one saved; regenerating VCS_MEDIA_TOKEN_SECRET
# invalidates every signed audio URL already handed out. So they are generated
# exactly once and persisted to /workspace, which survives a pod stop/start.
# They are also printed below in full — an $(...) inside the serve command would
# mint a fresh key on every launch and never show it to you, which is precisely
# the bug this replaces.
SECRETS_FILE="/workspace/vcs-secrets.env"
if [ ! -f "$SECRETS_FILE" ]; then
  umask 077
  python3 - <<'PY' > "$SECRETS_FILE"
import secrets
print("VCS_API_KEY=" + secrets.token_hex(32))
print("VCS_MEDIA_TOKEN_SECRET=" + secrets.token_hex(32))
PY
  chmod 600 "$SECRETS_FILE"
  echo "   generated $SECRETS_FILE (first run on this volume)"
else
  echo "   reusing existing $SECRETS_FILE — NOT regenerating"
fi
# shellcheck disable=SC1090
set -a; source "$SECRETS_FILE"; set +a

echo "== 9. research lab =="
mkdir -p /workspace/engines-lab/{r1-f5,r2-chatterbox,r3-voxcpm,r4-urdu}
cp /workspace/engines-lab-ENV.sh /workspace/engines-lab/ENV.sh
touch /workspace/engines-lab/.gpu.lock   # serializes GPU access between agents

echo "== 10. verify =="
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
python3 --version
# VCS_API_KEY must NOT be visible here. Step 8 exports it, and the API-key
# middleware reads it from the environment — leaving it set makes every test
# request 401 and the suite fails with a misleading `assert 401 == 400`. The
# unset is explicit rather than relying on step order, since a stale value in
# /root/.bashrc would do the same thing.
cd "$REPO_DIR/backend" && env -u VCS_API_KEY -u VCS_MEDIA_TOKEN_SECRET \
  uv run pytest -q -m "not gpu" 2>&1 | tail -3
df -h / /workspace | tail -2

echo "== 11. ngrok (OPTIONAL — only if NGROK_AUTHTOKEN is set) =="
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

echo "== 12. current status =="
# Bootstrap provisions; it does not start anything. On a FIRST run both of these
# are expected to be down. The value is on a RE-RUN against a live pod, where it
# answers "is the thing I already started still up?" without a second SSH trip.
BACKEND_UP=0
if curl -sf -m 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  BACKEND_UP=1
  echo "   backend:  UP   (127.0.0.1:8000)"
else
  echo "   backend:  DOWN — start it with the command below"
fi

NGROK_UP=0
if pgrep -x ngrok >/dev/null 2>&1; then
  NGROK_UP=1
  # The agent's local API knows the real public URL, which is the only way to
  # catch "ngrok is running, but on a different domain than you think".
  PUBLIC_URL="$(curl -sf -m 3 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["tunnels"][0]["public_url"])' 2>/dev/null || true)"
  echo "   ngrok:    UP   ${PUBLIC_URL:-(agent API unreachable)}"
else
  echo "   ngrok:    DOWN — start it with the command below"
fi

# Only meaningful when both halves are up: proves the tunnel actually reaches
# this backend, which neither check above can tell you on its own.
if [ "$BACKEND_UP" = 1 ] && [ "$NGROK_UP" = 1 ] && [ -n "${NGROK_DOMAIN:-}" ]; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 10 \
    -H 'ngrok-skip-browser-warning: true' \
    "https://${NGROK_DOMAIN}/api/health" 2>/dev/null || echo 000)"
  if [ "$CODE" = "200" ]; then
    echo "   public:   OK   https://${NGROK_DOMAIN}/api/health -> 200"
  else
    echo "   public:   FAIL https://${NGROK_DOMAIN}/api/health -> HTTP ${CODE}"
    echo "             (404 = tunnel not connected; 502 = tunnel up but backend refused)"
  fi
fi

CORS_ORIGIN="${FRONTEND_URL:-https://YOUR-PAGES-URL.pages.dev}"

echo
echo "== READY =="
echo "  repo:    $REPO_DIR ($BRANCH)"
echo "  runtime: $VOX_VENV  (torch cu128)"
echo "  lab:     /workspace/engines-lab/"
echo "  caches:  /workspace/{hf,torch,pip,uv}-cache"
echo "  secrets: $SECRETS_FILE"
echo
echo "== YOUR SECRETS (save these — same values on every restart) =="
echo "  VCS_API_KEY=$VCS_API_KEY"
echo "  VCS_MEDIA_TOKEN_SECRET=$VCS_MEDIA_TOKEN_SECRET"
echo "  Paste VCS_API_KEY into the frontend's settings gear."
echo
if [ "$CORS_ORIGIN" = "https://YOUR-PAGES-URL.pages.dev" ]; then
  echo "  NOTE: pass FRONTEND_URL=https://... to bake your real origin into the"
  echo "        command below; otherwise edit VCS_CORS_ORIGINS before running it."
  echo
fi
echo "Start serving (detached, survives your SSH session closing):"
echo "  tmux new-session -d -s backend \"cd $REPO_DIR/backend && \\"
echo "    set -a && source $SECRETS_FILE && set +a && \\"
echo "    HF_HOME=/workspace/hf-cache \\"
echo "    VCS_CORS_ORIGINS='[\\\"$CORS_ORIGIN\\\"]' \\"
echo "    VCS_WARM_ON_STARTUP=voxcpm2 \\"
echo "    VCS_VOXCPM_PYTHON=$VOX_VENV/bin/python \\"
echo "    VCS_WORKER_CWD=$REPO_DIR/backend \\"
echo "    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 2>&1 | tee /workspace/backend.log\""
echo
echo "  (sourcing $SECRETS_FILE is what keeps the key stable — do not inline a"
echo "   freshly generated one, or every saved frontend key starts 401ing.)"
echo
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  echo "Then start ngrok in another session:"
  echo "  ngrok http --domain=${NGROK_DOMAIN} 8000"
  echo
fi
echo "Reach it from your laptop with SSH tunnel (alternative to ngrok):"
echo "  ssh -N -L 8000:127.0.0.1:8000 -p <POD_PORT> root@<POD_HOST>"
