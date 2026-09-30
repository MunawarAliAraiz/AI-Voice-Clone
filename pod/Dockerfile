# syntax=docker/dockerfile:1
# Build context is the repository root. Both inputs MUST include @sha256:...
# See docs/POD_WORKER.md. Neither model weights nor secrets enter this image.
ARG PYTHON_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE}

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
RUN uv venv /opt/venvs/api --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/api/bin/python --require-hashes --only-binary :all: /opt/vcs/requirements/api.lock \
    && uv venv /opt/venvs/voxcpm --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/voxcpm/bin/python --require-hashes --only-binary :all: /opt/vcs/requirements/build.lock \
    && uv pip sync --python /opt/venvs/voxcpm/bin/python --torch-backend cu128 --require-hashes --no-build-isolation --only-binary :all: --no-binary argbind,oss2 /opt/vcs/requirements/voxcpm.lock \
    && uv venv /opt/venvs/chatterbox --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/chatterbox/bin/python --torch-backend cu124 --require-hashes --only-binary :all: /opt/vcs/requirements/chatterbox.lock \
    && uv venv /opt/venvs/omnivoice --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/omnivoice/bin/python --torch-backend cu128 --require-hashes --only-binary :all: /opt/vcs/requirements/omnivoice.lock \
    && uv venv /opt/venvs/text --python /usr/local/bin/python \
    && uv pip sync --python /opt/venvs/text/bin/python --torch-backend cu128 --require-hashes --only-binary :all: /opt/vcs/requirements/text.lock \
    && uv cache clean
COPY backend/app/ /opt/vcs/app/
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
