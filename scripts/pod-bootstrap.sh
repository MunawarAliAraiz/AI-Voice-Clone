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
#
# The full real-world invocation — provision, then start backend + ngrok, with
# the public domain and the frontend origin remembered on /workspace so a later
# re-run needs neither:
#
#   ssh root@HOST -p PORT "START=1 NGROK_AUTHTOKEN=... NGROK_DOMAIN=your.ngrok-free.dev \
#     FRONTEND_URL=https://your-frontend.example bash -s" < scripts/pod-bootstrap.sh
#
# Carry your EXISTING secrets onto a fresh pod (otherwise a new /workspace mints
# a new VCS_API_KEY and every browser must be re-paired — see step 15):
#
#   ... VCS_API_KEY=... VCS_MEDIA_TOKEN_SECRET=... bash -s" < scripts/pod-bootstrap.sh
#
# Provisions five Python envs, ~15GB of weights, and takes 15-30 min on a cold
# /workspace. Re-running against a warm one is fast and is the intended way to
# restart things — every step is idempotent.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/MunawarAliAraiz/AI-Voice-Clone.git}"
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
command -v tmux   >/dev/null || { apt-get update -qq && apt-get install -y -qq tmux; }
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

echo "== 8. Chatterbox runtime env (separate interpreter, same reason as VoxCPM) =="
# Not routable yet (LanguageSupport cells unverified — Phase 4c), but the
# venv + weights are worth having warm so a pod is immediately useful for
# Phase 4b/4c work rather than a 5-minute setup tax every time.
CB_VENV="$REPO_DIR/backend/.venv-chatterbox"
if [ ! -x "$CB_VENV/bin/python" ]; then
  uv venv "$CB_VENV" --python 3.12
fi
uv pip install --python "$CB_VENV" chatterbox-tts 2>&1 | tail -2
# Same cu130-silent-CPU trap as VoxCPM (step 6) — pin cu128 explicitly.
uv pip install --python "$CB_VENV" \
  torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 2>&1 | tail -2
# CRITICAL, cost real debugging time (2026-08-11): chatterbox-tts depends on
# resemble-perth for audio watermarking, whose perth_net/__init__.py does
# `from pkg_resources import resource_filename` — an API setuptools>=81
# removed outright. `uv venv` pulls whatever setuptools uv itself ships,
# which was already past that line. The failure is SILENT at import time:
# perth/__init__.py wraps the submodule import in a bare `except ImportError:
# PerthImplicitWatermarker = None`, so `import perth` succeeds and the real
# error only surfaces as `TypeError: 'NoneType' object is not callable` deep
# inside ChatterboxMultilingualTTS.from_local(), nowhere near the real cause.
uv pip install --python "$CB_VENV" "setuptools<81" 2>&1 | tail -2
echo "   torch CUDA visible:"
"$CB_VENV/bin/python" -c "import torch; print('   ->', torch.__version__, 'cuda', torch.cuda.is_available())"

echo "== 9. Chatterbox weights (pinned revision, cached on /workspace) =="
# Only the files ChatterboxMultilingualTTS.from_local() actually reads —
# verified from its source, Phase 4b (see docs/PHASE4_CHATTERBOX_DESIGN.md §3).
# Deliberately NOT calling from_pretrained(): it hardcodes revision="main"
# internally and would silently ignore this pin (golden rule 7).
CB_REV="5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
"$CB_VENV/bin/python" - "$CB_REV" <<'PY' 2>&1 | tail -2
import sys
from huggingface_hub import snapshot_download
p = snapshot_download(
    "ResembleAI/chatterbox", revision=sys.argv[1],
    allow_patterns=["ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt",
                    "grapheme_mtl_merged_expanded_v1.json", "conds.pt"],
)
print("   weights at", p)
PY

echo "== 10. OmniVoice runtime env (separate interpreter, same reason as VoxCPM) =="
# CC-BY-NC weights — personal use only, see docs/URDU_MODEL_LICENSING.md and
# golden rule 6's 2026-08-15 amendment. Its (ur, ARABIC) cell is verified=True
# as of 2026-08-15, so this venv is REQUIRED, not just convenient: an explicit
# model_id=omnivoice_urdu request fails without it. Auto-routing still never
# picks it — ModelCatalog.candidates() excludes non-permissive licences even
# when verified — but the picker offers it and the owner uses it by name.
OV_VENV="$REPO_DIR/backend/.venv-omnivoice"
if [ ! -x "$OV_VENV/bin/python" ]; then
  uv venv "$OV_VENV" --python 3.12
fi
uv pip install --python "$OV_VENV" omnivoice 2>&1 | tail -2
# Same cu130-silent-CPU trap as VoxCPM (step 6) and Chatterbox (step 8) — pin
# cu128 explicitly rather than trust whatever the package resolves.
uv pip install --python "$OV_VENV" \
  torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 2>&1 | tail -2
echo "   torch CUDA visible:"
"$OV_VENV/bin/python" -c "import torch; print('   ->', torch.__version__, 'cuda', torch.cuda.is_available())"

echo "== 11. OmniVoice weights (pinned revision, ~1.2GB, cached on /workspace) =="
# Revision resolved during the bake-off (2026-08-14), recorded in
# catalog.py's OMNIVOICE_URDU spec and both arm-E manifests. Passed through
# OmniVoiceBackend.load()'s own from_pretrained(revision=...) call — NOT yet
# pod-verified that OmniVoice actually honors this kwarg the way Chatterbox's
# from_pretrained() silently does NOT (see chatterbox.py's module docstring
# for that exact trap). If the Phase 1 smoke test shows it's ignored, switch
# to snapshot_download + a local-path load, same bypass chatterbox.py uses.
OV_REV="c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
"$OV_VENV/bin/python" - "$OV_REV" <<'PY' 2>&1 | tail -2
import sys
from huggingface_hub import snapshot_download
p = snapshot_download("k2-fsa/OmniVoice", revision=sys.argv[1])
print("   weights at", p)
PY

echo "== 12. Qwen Speech-Direction analyzer env (NOT an audio runtime) =="
# Powers POST /api/direction/analyze-llm — the "Let AI suggest emotion/tone"
# button in the Advanced editor. Without this venv the endpoint raises
# (analyzer_scheduler.py: "set VCS_QWEN_ANALYZER_PYTHON") and that button
# fails on a fresh pod, which is exactly how this step came to be missing
# for three days after the feature shipped.
#
# Deliberately NOT a RuntimeKind and NOT in Settings.interpreters(): this
# classifies text, never synthesizes audio, and must stay unreachable from
# domain/routing.py's resolve(). AnalyzerScheduler reads its own setting.
# Same venv the eval probes use (eval/run_qwen_analyzer_probe.py,
# run_translit_probe.py, run_roman_arabic_probe.py) — one Qwen stack, not two.
QWEN_VENV="$REPO_DIR/backend/.venv-qwen"
if [ ! -x "$QWEN_VENV/bin/python" ]; then
  uv venv "$QWEN_VENV" --python 3.12
fi
# Same cu130-silent-CPU trap as every other runtime above — pin cu128.
uv pip install --python "$QWEN_VENV" \
  torch --index-url https://download.pytorch.org/whl/cu128 2>&1 | tail -2
# `jiwer` is only needed by the transliteration probes, not by production —
# harmless and small, and it keeps this venv able to run every eval/ Qwen
# script without a second provisioning step.
uv pip install --python "$QWEN_VENV" transformers accelerate jiwer 2>&1 | tail -2
echo "   torch CUDA visible:"
"$QWEN_VENV/bin/python" -c "import torch; print('   ->', torch.__version__, 'cuda', torch.cuda.is_available())"

echo "== 13. Qwen analyzer weights (pinned revision, ~6GB, cached on /workspace) =="
# Pinned per golden rule 7. Must match QWEN_ANALYZER_HF_REVISION in
# backend/app/inference/analyzer_scheduler.py — if you bump one, bump both.
QWEN_REV="aa8e72537993ba99e69dfaafa59ed015b17504d1"
"$QWEN_VENV/bin/python" - "$QWEN_REV" <<'PY' 2>&1 | tail -2
import sys
from huggingface_hub import snapshot_download
p = snapshot_download("Qwen/Qwen2.5-3B-Instruct", revision=sys.argv[1])
print("   weights at", p)
PY

echo "== 14. eval harness env (Whisper CER + ECAPA speaker cosine, Phase A/4c) =="
# A separate venv from every runtime, deliberately: the harness's own deps
# (transformers, speechbrain) have no reason to co-resolve with a runtime's
# torch pin, and mixing them risks silently upgrading the runtime's torch
# (see docs/PHASE_A_RESULTS.md's note on .venv-eval drifting to 2.13.0+cu130
# on an earlier pod).
EVAL_VENV="$REPO_DIR/backend/.venv-eval"
if [ ! -x "$EVAL_VENV/bin/python" ]; then
  uv venv "$EVAL_VENV" --python 3.12
fi
uv pip install --python "$EVAL_VENV" torch torchaudio \
  --index-url https://download.pytorch.org/whl/cu128 2>&1 | tail -2
uv pip install --python "$EVAL_VENV" \
  transformers speechbrain jiwer soundfile accelerate 2>&1 | tail -2
# DO NOT install torchcodec here. torchaudio.load() and the transformers ASR
# pipeline both prefer it when present, but its prebuilt binaries are linked
# against a specific CUDA runtime (observed: needs libnvrtc.so.13) independent
# of whichever CUDA build of torch is actually installed — a real crash, not a
# warning, and installing torchcodec to silence the first ImportError makes it
# WORSE (transformers then prefers the broken binary over its working
# fallback). eval_harness.py reads audio via soundfile everywhere for exactly
# this reason — don't "fix" a torchcodec ModuleNotFoundError by installing it.
"$EVAL_VENV/bin/python" -c "import torch; print('   ->', torch.__version__, 'cuda', torch.cuda.is_available())"

echo "== 15. secrets (generated once, reused on every restart) =="
# These MUST be stable across restarts. Regenerating VCS_API_KEY 401s every
# frontend that has the old one saved; regenerating VCS_MEDIA_TOKEN_SECRET
# invalidates every signed audio URL already handed out. So they are generated
# exactly once and persisted to /workspace, which survives a pod stop/start.
# They are also printed below in full — an $(...) inside the serve command would
# mint a fresh key on every launch and never show it to you, which is precisely
# the bug this replaces.
#
# A FRESH /workspace has no secrets file, so a brand-new pod would otherwise
# mint a new key and force every browser to be re-paired. Pass VCS_API_KEY and
# VCS_MEDIA_TOKEN_SECRET explicitly to carry your existing pair onto the new
# pod and skip that entirely — the frontend never notices the move.
SECRETS_FILE="/workspace/vcs-secrets.env"
if [ -n "${VCS_API_KEY:-}" ] && [ -n "${VCS_MEDIA_TOKEN_SECRET:-}" ]; then
  umask 077
  printf 'VCS_API_KEY=%s\nVCS_MEDIA_TOKEN_SECRET=%s\n' \
    "$VCS_API_KEY" "$VCS_MEDIA_TOKEN_SECRET" > "$SECRETS_FILE"
  chmod 600 "$SECRETS_FILE"
  echo "   using the secrets you passed in (saved to $SECRETS_FILE)"
elif [ -n "${VCS_API_KEY:-}" ] || [ -n "${VCS_MEDIA_TOKEN_SECRET:-}" ]; then
  # Half a pair is always a mistake: supplying only the API key would silently
  # regenerate the media secret and 403 every audio URL already handed out.
  echo "   ERROR: pass BOTH VCS_API_KEY and VCS_MEDIA_TOKEN_SECRET, or neither." >&2
  exit 1
elif [ ! -f "$SECRETS_FILE" ]; then
  umask 077
  python3 - <<'PY' > "$SECRETS_FILE"
import secrets
print("VCS_API_KEY=" + secrets.token_hex(32))
print("VCS_MEDIA_TOKEN_SECRET=" + secrets.token_hex(32))
PY
  chmod 600 "$SECRETS_FILE"
  echo "   generated $SECRETS_FILE (first run on this volume)"
  echo "   NOTE: this is a NEW key — paste it into the frontend's settings gear."
else
  echo "   reusing existing $SECRETS_FILE — NOT regenerating"
fi
# shellcheck disable=SC1090
set -a; source "$SECRETS_FILE"; set +a

echo "== 16. research lab =="
mkdir -p /workspace/engines-lab/{r1-f5,r2-chatterbox,r3-voxcpm,r4-urdu}
cp /workspace/engines-lab-ENV.sh /workspace/engines-lab/ENV.sh
touch /workspace/engines-lab/.gpu.lock   # serializes GPU access between agents

echo "== 17. verify =="
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

echo "== 18. ngrok (OPTIONAL — only if NGROK_AUTHTOKEN is set) =="
# Remember the domain the same way FRONTEND_URL is remembered. A restart that
# forgets it does not fail loudly — ngrok happily allocates a RANDOM url, which
# the deployed frontend has no way to reach, so the app looks broken for a
# reason nothing in its logs mentions.
NGROK_FILE="/workspace/vcs-ngrok-domain"
if [ -n "${NGROK_DOMAIN:-}" ]; then
  printf '%s\n' "$NGROK_DOMAIN" > "$NGROK_FILE"
elif [ -f "$NGROK_FILE" ]; then
  NGROK_DOMAIN="$(cat "$NGROK_FILE")"
fi
# Define unconditionally: every later reference runs under `set -u`, and an
# unset NGROK_DOMAIN aborted the whole script with "unbound variable".
NGROK_DOMAIN="${NGROK_DOMAIN:-}"

if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  if ! command -v ngrok >/dev/null; then
    echo "   installing ngrok..."
    curl -sSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz | tar -xz -C /usr/local/bin
  fi

  ngrok config add-authtoken "$NGROK_AUTHTOKEN"
  if [ -n "$NGROK_DOMAIN" ]; then
    echo "   ngrok domain: $NGROK_DOMAIN"
    echo "   start it with:"
    echo "     nohup ngrok http --domain=$NGROK_DOMAIN 8000 > /workspace/ngrok.log 2>&1 &"
  else
    echo "   WARNING: no NGROK_DOMAIN given and none remembered — ngrok would get a"
    echo "            random URL your frontend cannot reach. Pass NGROK_DOMAIN once."
  fi
else
  echo "   skipped (NGROK_AUTHTOKEN not set)"
fi

echo "== 19. serve script (CORS baked in, remembered across runs) =="
# CORS is the easiest thing to get wrong here, and it fails in the least
# obvious way: the origin must match the deployed frontend EXACTLY — scheme,
# host, no trailing slash — or every request dies in preflight with "No
# 'Access-Control-Allow-Origin' header", which reads like the backend is down
# rather than misconfigured. Persisting it next to the secrets means a restart
# cannot silently fall back to the placeholder, which is precisely how a live
# backend ended up rejecting its own frontend.
FRONTEND_FILE="/workspace/vcs-frontend-url"
if [ -n "${FRONTEND_URL:-}" ]; then
  printf '%s\n' "${FRONTEND_URL%/}" > "$FRONTEND_FILE"   # strip trailing slash
  echo "   frontend origin saved: $(cat "$FRONTEND_FILE")"
elif [ -f "$FRONTEND_FILE" ]; then
  FRONTEND_URL="$(cat "$FRONTEND_FILE")"
  echo "   frontend origin remembered: $FRONTEND_URL"
else
  echo "   WARNING: no FRONTEND_URL yet — pass FRONTEND_URL=https://... once and"
  echo "            it is remembered from then on. Until then CORS will reject"
  echo "            your real frontend."
fi
CORS_ORIGIN="${FRONTEND_URL:-https://YOUR-PAGES-URL.pages.dev}"

cat > /workspace/serve.sh <<SERVEEOF
#!/usr/bin/env bash
# Generated by pod-bootstrap.sh — re-run it to regenerate.
set -euo pipefail
set -a; source /workspace/vcs-secrets.env; set +a
export HF_HOME=/workspace/hf-cache
export VCS_CORS_ORIGINS='["${CORS_ORIGIN}"]'
export VCS_WARM_ON_STARTUP=voxcpm2
export VCS_VOXCPM_PYTHON=${VOX_VENV}/bin/python
# Chatterbox is not yet routable (LanguageSupport cells unverified — Phase
# 4c), but pointing this at the venv now means routing needs no redeploy the
# moment a cell flips verified=True.
export VCS_CHATTERBOX_PYTHON=${CB_VENV}/bin/python
# omnivoice_urdu's (ur, ARABIC) cell is verified=True (2026-08-15), so this
# one is load-bearing, not speculative: without it every explicit
# model_id=omnivoice_urdu request fails with "no interpreter configured for
# runtime 'omnivoice'".
export VCS_OMNIVOICE_PYTHON=${OV_VENV}/bin/python
# Speech Direction's LLM analyzer (POST /api/direction/analyze-llm, the "Let
# AI suggest emotion/tone" button). Deliberately NOT a RuntimeKind — see
# step 12 and analyzer_scheduler.py's docstring.
export VCS_QWEN_ANALYZER_PYTHON=${QWEN_VENV}/bin/python
export VCS_WORKER_CWD=${REPO_DIR}/backend
cd ${REPO_DIR}/backend
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
SERVEEOF
chmod +x /workspace/serve.sh
echo "   wrote /workspace/serve.sh (CORS origin: $CORS_ORIGIN)"

if [ "${START:-0}" = "1" ]; then
  echo "   START=1 — (re)starting backend and ngrok in tmux"
  tmux kill-session -t backend 2>/dev/null || true
  tmux new-session -d -s backend "/workspace/serve.sh 2>&1 | tee /workspace/backend.log"
  if [ -n "${NGROK_DOMAIN:-}" ] && command -v ngrok >/dev/null; then
    tmux kill-session -t ngrok 2>/dev/null || true
    tmux new-session -d -s ngrok "ngrok http --domain=${NGROK_DOMAIN} 8000 > /workspace/ngrok.log 2>&1"
  fi
  # Model warm-up is slow; poll rather than guess a sleep duration.
  for _ in $(seq 30); do
    curl -sf -m 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
    sleep 2
  done
fi

echo "== 20. current status =="
# Without START=1 bootstrap only provisions, so on a first run both of these are
# expected to be down. The value is on a RE-RUN against a live pod, where it
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

echo
echo "== READY =="
echo "  repo:    $REPO_DIR ($BRANCH)"
echo "  runtime: $VOX_VENV  (torch cu128, routable — English + Roman Urdu)"
echo "  runtime: $OV_VENV  (torch cu128, routable by explicit pick — Perso-Arabic Urdu)"
echo "  runtime: $CB_VENV  (torch cu128, NOT routable — failed its identity listen, Phase 4c)"
echo "  analyzer:$QWEN_VENV  (torch cu128, Speech Direction 'suggest emotion/tone')"
echo "  eval:    $EVAL_VENV  (torch cu128, Whisper CER + ECAPA speaker cosine)"
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
  echo "  NOTE: FRONTEND_URL was never set, so CORS will reject your real"
  echo "        frontend. Re-run once with FRONTEND_URL=https://... and it is"
  echo "        remembered in $FRONTEND_FILE from then on."
  echo
fi
echo "Start serving (detached, survives your SSH session closing):"
echo "  tmux new-session -d -s backend '/workspace/serve.sh 2>&1 | tee /workspace/backend.log'"
echo
echo "  Or re-run this bootstrap with START=1 to start backend + ngrok for you."
echo "  serve.sh has the secrets and the CORS origin baked in, so a restart"
echo "  cannot drift from what the frontend expects."
echo
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  echo "Then start ngrok in another session:"
  echo "  ngrok http --domain=${NGROK_DOMAIN} 8000"
  echo
fi
echo "Reach it from your laptop with SSH tunnel (alternative to ngrok):"
echo "  ssh -N -L 8000:127.0.0.1:8000 -p <POD_PORT> root@<POD_HOST>"
