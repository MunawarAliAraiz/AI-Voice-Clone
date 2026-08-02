# ---------------------------------------------------------------
# AI Voice Clone Studio - Backend Dockerfile
# ---------------------------------------------------------------
# Build:   docker build -t voice-clone-backend ./backend
# Run:     docker run -p 8000:8000 --gpus all voice-clone-backend
# ---------------------------------------------------------------

# Use NVIDIA CUDA base image for GPU support
# Swap to python:3.12-slim for CPU-only deployments
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-dev \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

# Install all dependencies including GPU extras
RUN uv sync --frozen --extra gpu --no-dev

COPY . .

RUN mkdir -p data/voices data/generated data/models data/cache data/db logs

ENV VCS_HOST=0.0.0.0
ENV VCS_PORT=8000
ENV VCS_DEBUG=false
ENV VCS_DEFAULT_ENGINE=f5_tts

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["uv", "run", "python", "run.py"]
