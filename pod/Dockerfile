# Build context is the repository root. Both inputs MUST include @sha256:...
# See docs/POD_WORKER.md. Neither model weights nor secrets enter this image.
ARG PYTHON_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS base

ARG DEBIAN_SNAPSHOT=20260901T000000Z
ENV DEBIAN_FRONTEND=noninteractive
RUN printf 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/%s/ bookworm main\n' "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
RUN python -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && uv --version | grep -q '^uv 0.11.32 '
COPY pod/requirements/ /opt/vcs/requirements/
FROM base AS api
RUN uv venv /opt/venvs/api --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/api/bin/python --require-hashes --only-binary :all: /opt/vcs/requirements/api.lock \
    && uv cache clean
COPY backend/app/ /opt/vcs/app/
COPY pod/healthcheck.py /opt/vcs/pod/
ENV PYTHONPATH=/opt/vcs PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/workspace/hf-cache HF_HUB_CACHE=/workspace/hf-cache/hub \
    HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_PROGRESS_BARS=1 \
    VCS_MODEL_VOLUME_ROOT=/workspace
WORKDIR /opt/vcs
RUN /opt/venvs/api/bin/python -c "import importlib.util; assert importlib.util.find_spec('torch') is None; from app.remote_worker.main import create_worker_app"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["/opt/venvs/api/bin/python", "/opt/vcs/pod/healthcheck.py"]

FROM api AS installer
RUN /opt/venvs/api/bin/python -c "from app.remote_worker.installer_main import create_installer_app"
ENTRYPOINT ["/opt/venvs/api/bin/python", "-m", "uvicorn", "app.remote_worker.installer_main:create_installer_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

FROM api AS gpu
# Identical CUDA 12.8 wheels can be hardlinked between immutable environments
# only while uv's cache and all installations share this same build layer.
# Keep interpreter isolation; remove the download cache after all envs exist.
# Five pinned VoxCPM dependencies have no Python 3.12 Linux wheels. Permit
# only their hashed source archives; all other dependencies remain wheel-only.
RUN export UV_LINK_MODE=hardlink \
    && uv venv /opt/venvs/voxcpm --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/voxcpm/bin/python --require-hashes --only-binary :all: /opt/vcs/requirements/build.lock \
    && uv pip sync --python /opt/venvs/voxcpm/bin/python --torch-backend cu128 --require-hashes --no-build-isolation --only-binary :all: --no-binary antlr4-python3-runtime,argbind,crcmod,jieba,oss2 /opt/vcs/requirements/voxcpm.lock \
    && uv venv /opt/venvs/chatterbox --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/chatterbox/bin/python --torch-backend cu128 --require-hashes --only-binary :all: /opt/vcs/requirements/chatterbox.lock \
    && uv venv /opt/venvs/omnivoice --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/omnivoice/bin/python --torch-backend cu128 --require-hashes --only-binary :all: /opt/vcs/requirements/omnivoice.lock \
    && uv venv /opt/venvs/text --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/text/bin/python --torch-backend cu128 --require-hashes --only-binary :all: /opt/vcs/requirements/text.lock \
    && uv cache clean
COPY pod/start.py pod/preflight.py pod/healthcheck.py /opt/vcs/pod/
ENV PYTHONPATH=/opt/vcs PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    HF_HOME=/workspace/hf-cache HF_HUB_CACHE=/workspace/hf-cache/hub \
    TORCH_HOME=/workspace/torch-cache TORCHINDUCTOR_CACHE_DIR=/workspace/torch-inductor \
    HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_PROGRESS_BARS=1 \
    VCS_VOXCPM_PYTHON=/opt/venvs/voxcpm/bin/python \
    VCS_CHATTERBOX_PYTHON=/opt/venvs/chatterbox/bin/python \
    VCS_OMNIVOICE_PYTHON=/opt/venvs/omnivoice/bin/python \
    VCS_QWEN_ANALYZER_PYTHON=/opt/venvs/text/bin/python \
    VCS_GEMMA_TRANSLITERATOR_PYTHON=/opt/venvs/text/bin/python \
    VCS_WORKER_CWD=/opt/vcs VCS_BUDGET_MB=16000 VCS_MAX_WORKERS=2
WORKDIR /opt/vcs
RUN /opt/venvs/api/bin/python /opt/vcs/pod/preflight.py --imports-only
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["/opt/venvs/api/bin/python", "/opt/vcs/pod/healthcheck.py"]
ENTRYPOINT ["/opt/venvs/api/bin/python", "/opt/vcs/pod/start.py"]
