# AI Voice Clone Studio — API image.
#
# CPU-ONLY, BY DESIGN. `import torch` is not reachable from the API process; the
# GPU lives in worker subprocesses that run their own CUDA venvs. So this image
# is a slim python base, not a multi-gigabyte CUDA one. A GPU deployment runs
# this API container plus the worker venvs (a separate CUDA image) and points
# VCS_VOXCPM_PYTHON at the worker interpreter.
#
# Build: docker build -t voice-clone-api ./backend
# Run:   docker run -p 8000:8000 -v $PWD/data:/data voice-clone-api
#
# NOTE: not build-tested in this environment — verify `docker build` before use.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# uv, copied from its official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install deps first for layer caching. uv.lock is optional (COPY glob).
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev

COPY . .

ENV VCS_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# ONE uvicorn worker: N workers = N schedulers = N × VRAM.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
