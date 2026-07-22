"""
AI Voice Clone Studio — FastAPI Backend Entry Point

Start with: python -m app.main
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .database import init_database
from .engines import get_engine
from .routers import voice, tts, history, settings as settings_router
from .utils.logger import setup_logger

logger = setup_logger("voiceclone.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup and shutdown lifecycle."""
    # ── Startup ──
    logger.info("=" * 60)
    logger.info(f"🚀 {settings.app_name} v{settings.app_version}")
    logger.info("=" * 60)

    # Create directories
    settings.ensure_directories()
    logger.info("📁 Directories ready")

    # Initialize database
    await init_database()
    logger.info("🗄️  Database ready")

    # Load mock engine (always available for development)
    mock = get_engine("mock")
    await mock.load_model()
    logger.info("🤖 Mock engine loaded (development mode)")

    # Try loading real engines (will silently fail if packages aren't installed)
    try:
        from .utils.gpu import get_gpu_info
        gpu = get_gpu_info()
        if gpu.available:
            logger.info(f"🎮 GPU: {gpu.name} ({gpu.vram_total_mb} MB VRAM)")
        else:
            logger.info("💻 Running in CPU mode (no CUDA GPU detected)")
    except Exception:
        logger.info("💻 Running in CPU mode")

    # Log security status
    if settings.api_key:
        logger.info("🔒 API key authentication: ENABLED")
    else:
        logger.info("🔓 API key authentication: disabled (set VCS_API_KEY to enable)")

    logger.info(f"🌐 Server: http://{settings.host}:{settings.port}")
    logger.info(f"📖 API Docs: http://{settings.host}:{settings.port}/docs")
    logger.info("=" * 60)

    yield

    # ── Shutdown ──
    logger.info("Shutting down...")


# ── Build CORS origins list ──

def _build_cors_origins() -> list[str]:
    """Combine static origins with wildcard for SSH tunneling / remote web access."""
    base_origins = [
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "https://tauri.localhost",
        "http://tauri.localhost",
        "*",
    ]

    if settings.cors_origins:
        extra = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        base_origins.extend(extra)

    return base_origins


# ── Create FastAPI app ──

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Personal voice cloning and multilingual text-to-speech",
    lifespan=lifespan,
)

# CORS middleware — allow Tauri frontend, dev server, and any extra configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Optional API Key Middleware ──

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Enforce API key authentication when VCS_API_KEY is configured.

    Pass the key in the request header:
        X-API-Key: your_secret_key

    If VCS_API_KEY is empty, this middleware is a no-op (open access).
    The /docs and /openapi.json endpoints are always accessible.
    """
    if settings.api_key:
        # Always allow docs and health check without auth
        open_paths = {"/", "/docs", "/openapi.json", "/redoc"}
        if request.url.path not in open_paths:
            provided_key = request.headers.get("X-API-Key", "")
            if provided_key != settings.api_key:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "Invalid or missing API key. "
                                  "Pass your key in the X-API-Key request header."
                    },
                )

    return await call_next(request)


# ── Register routers ──

app.include_router(voice.router)
app.include_router(tts.router)
app.include_router(history.router)
app.include_router(settings_router.router)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


