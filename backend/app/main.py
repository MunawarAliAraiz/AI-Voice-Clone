"""
AI Voice Clone Studio — FastAPI Backend Entry Point

Start with: python -m app.main
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

    logger.info(f"🌐 Server: http://{settings.host}:{settings.port}")
    logger.info(f"📖 API Docs: http://{settings.host}:{settings.port}/docs")
    logger.info("=" * 60)

    yield

    # ── Shutdown ──
    logger.info("Shutting down...")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Personal voice cloning and multilingual text-to-speech",
    lifespan=lifespan,
)

# CORS — allow Tauri frontend and dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",   # Tauri dev server
        "http://localhost:5173",   # Vite dev server
        "tauri://localhost",       # Tauri production
        "https://tauri.localhost", # Tauri v2
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
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
