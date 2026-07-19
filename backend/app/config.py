"""
AI Voice Clone Studio — Configuration Management
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # App
    app_name: str = "AI Voice Clone Studio"
    app_version: str = "0.1.0"
    debug: bool = True

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # Paths — all relative to project root
    project_root: Path = Path(__file__).resolve().parent.parent.parent
    data_dir: Path = project_root / "data"
    voices_dir: Path = data_dir / "voices"
    profiles_dir: Path = data_dir / "profiles"
    generated_dir: Path = data_dir / "generated"
    models_dir: Path = data_dir / "models"
    cache_dir: Path = data_dir / "cache"
    db_dir: Path = data_dir / "db"
    logs_dir: Path = project_root / "logs"

    # Database
    db_path: Path = db_dir / "voiceclone.db"

    # Audio defaults
    default_sample_rate: int = 44100
    default_output_format: str = "wav"
    max_recording_duration_sec: int = 300  # 5 minutes
    max_upload_size_mb: int = 50

    # TTS Engine
    default_engine: str = "mock"  # mock / f5_tts / fish_speech / xtts_v2
    default_language: str = "en"

    # GPU
    use_gpu: bool = True  # Will auto-fallback to CPU if no CUDA
    gpu_device: str = "cuda:0"

    class Config:
        env_prefix = "VCS_"  # Voice Clone Studio
        env_file = ".env"

    def ensure_directories(self) -> None:
        """Create all required directories if they don't exist."""
        for dir_path in [
            self.voices_dir,
            self.profiles_dir,
            self.generated_dir,
            self.models_dir,
            self.cache_dir,
            self.db_dir,
            self.logs_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)


# Singleton settings instance
settings = Settings()
