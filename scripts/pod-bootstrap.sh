#!/usr/bin/env bash
# Bootstrap a FRESH GPU pod for this project, from zero.
#
# Pods are recreated often and their `/` is a 30GB ephemeral overlay that is
# wiped every time. This script rebuilds everything that is not in git, so a new
# pod is productive in one command instead of an hour of rediscovery.
#
# USAGE (from your laptop, piping over ssh):
#
#   GH_USER=<user> GH_TOKEN=<token> bash scripts/pod-bootstrap.sh    # on the pod
#
# or remotely:
#
#   ssh root@HOST -p PORT "GH_USER=u GH_TOKEN=t bash -s" < scripts/pod-bootstrap.sh
#
# The token is read from the environment and written only to /root (ephemeral,
# chmod 600). It is NEVER written to /workspace, which persists and is snapshotted.

set -euo pipefail

REPO_URL="https://github.com/IftikharAhmedDev/AI-Voice-Clone.git"
REPO_DIR="/workspace/AI-Voice-Clone"
BRANCH="${BRANCH:-rewrite/contracts}"

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
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" remote set-url origin "$REPO_URL"   # keep the remote token-free
  git -C "$REPO_DIR" fetch origin --prune
else
  git clone "$REPO_URL" "$REPO_DIR"
fi
git -C "$REPO_DIR" checkout "$BRANCH" 2>/dev/null \
  || git -C "$REPO_DIR" checkout -b "$BRANCH" --track "origin/$BRANCH"
git -C "$REPO_DIR" pull --ff-only origin "$BRANCH" || true

echo "== 4. system deps =="
command -v ffmpeg >/dev/null || { apt-get update -qq && apt-get install -y -qq ffmpeg; }
command -v flock  >/dev/null || { apt-get update -qq && apt-get install -y -qq util-linux; }
command -v uv     >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

echo "== 5. backend env (NO torch — that is the structural invariant) =="
cd "$REPO_DIR/backend" && uv sync --python 3.12

echo "== 6. research lab =="
mkdir -p /workspace/engines-lab/{r1-f5,r2-chatterbox,r3-voxcpm,r4-urdu}
cp /workspace/engines-lab-ENV.sh /workspace/engines-lab/ENV.sh
touch /workspace/engines-lab/.gpu.lock   # serializes GPU access between agents

echo "== 7. verify =="
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
python3 --version
cd "$REPO_DIR/backend" && uv run pytest -q 2>&1 | tail -3
df -h / /workspace | tail -2

echo
echo "== READY =="
echo "  repo:   $REPO_DIR ($BRANCH)"
echo "  lab:    /workspace/engines-lab/"
echo "  caches: /workspace/{hf,torch,pip,uv}-cache"
echo
echo "If /workspace was NOT carried over, the model weights are gone and must be"
echo "re-downloaded (~7MB/s from HuggingFace). Everything else above is rebuilt."
