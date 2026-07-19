"""
AI Voice Clone Studio — Pydantic Schemas (Request/Response Models)
"""

from datetime import datetime
from pydantic import BaseModel, Field


# ── Voice Profile Schemas ──


class VoiceProfileCreate(BaseModel):
    """Request to create a voice profile."""
    name: str = Field(..., min_length=1, max_length=100, examples=["My Voice - English"])
    transcript: str | None = Field(None, examples=["Hello, I am recording my voice for cloning."])
    language: str = Field("en", examples=["en", "ur", "hi"])


class VoiceProfileResponse(BaseModel):
    """Voice profile response."""
    id: int
    name: str
    audio_path: str
    transcript: str | None
    language: str
    duration_sec: float | None
    sample_rate: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class VoiceProfileList(BaseModel):
    """List of voice profiles."""
    profiles: list[VoiceProfileResponse]
    total: int


# ── TTS Generation Schemas ──


class TTSGenerateRequest(BaseModel):
    """Request to generate speech."""
    text: str = Field(..., min_length=1, max_length=5000, examples=["Hello, this is a test."])
    profile_id: int = Field(..., examples=[1])
    language: str = Field("en", examples=["en", "ur", "hi"])
    engine: str = Field("auto", examples=["auto", "f5_tts", "fish_speech", "xtts_v2", "mock"])
    output_format: str = Field("wav", examples=["wav", "mp3"])


class TTSGenerateResponse(BaseModel):
    """TTS generation response."""
    id: int
    output_path: str
    duration_sec: float | None
    gen_time_sec: float
    engine: str
    language: str


class TTSLanguageInfo(BaseModel):
    """Supported language information."""
    code: str
    name: str
    engines: list[str]


# ── History Schemas ──


class HistoryItem(BaseModel):
    """A single generation history entry."""
    id: int
    profile_id: int
    profile_name: str | None = None
    input_text: str
    language: str
    engine: str
    output_path: str
    output_format: str
    duration_sec: float | None
    gen_time_sec: float | None
    is_favorite: bool
    created_at: datetime


class HistoryList(BaseModel):
    """Paginated history list."""
    items: list[HistoryItem]
    total: int
    page: int
    page_size: int


# ── Settings Schemas ──


class SettingItem(BaseModel):
    """A single setting."""
    key: str
    value: str
    category: str


class SettingsUpdate(BaseModel):
    """Update settings request."""
    settings: dict[str, str]


# ── Model Registry Schemas ──


class ModelInfo(BaseModel):
    """Information about an AI model."""
    id: int
    name: str
    engine: str
    version: str | None
    path: str
    size_mb: int | None
    languages: list[str] | None
    is_downloaded: bool
    is_active: bool


# ── System Schemas ──


class SystemStatus(BaseModel):
    """System health and status."""
    status: str = "ok"
    version: str
    gpu_available: bool
    gpu_name: str | None = None
    gpu_vram_mb: int | None = None
    active_engine: str
    profiles_count: int
    generations_count: int
