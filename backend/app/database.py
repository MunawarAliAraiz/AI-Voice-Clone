"""
AI Voice Clone Studio — Database Setup & Migrations
"""

import aiosqlite
from pathlib import Path
from .config import settings


SCHEMA_SQL = """
-- Voice profiles: your cloned voice references
CREATE TABLE IF NOT EXISTS voice_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    audio_path      TEXT NOT NULL,
    transcript      TEXT,
    language        TEXT NOT NULL DEFAULT 'en',
    duration_sec    REAL,
    sample_rate     INTEGER DEFAULT 44100,
    is_active       BOOLEAN DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Generation history: every TTS output you've created
CREATE TABLE IF NOT EXISTS generation_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES voice_profiles(id),
    input_text      TEXT NOT NULL,
    language        TEXT NOT NULL,
    engine          TEXT NOT NULL,
    output_path     TEXT NOT NULL,
    output_format   TEXT DEFAULT 'wav',
    duration_sec    REAL,
    gen_time_sec    REAL,
    is_favorite     BOOLEAN DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- App settings: key-value configuration store
CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    category        TEXT DEFAULT 'general',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Model registry: installed AI models
CREATE TABLE IF NOT EXISTS model_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    engine          TEXT NOT NULL,
    version         TEXT,
    path            TEXT NOT NULL,
    size_mb         INTEGER,
    languages       TEXT,
    is_downloaded   BOOLEAN DEFAULT 0,
    is_active       BOOLEAN DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Translation cache: Stores cached translations to avoid re-running NLLB-200
CREATE TABLE IF NOT EXISTS translation_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text     TEXT NOT NULL,
    source_lang     TEXT NOT NULL,
    target_lang     TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_text, source_lang, target_lang)
);


-- Insert default settings
INSERT OR IGNORE INTO settings (key, value, category) VALUES
    ('default_engine', 'mock', 'engine'),
    ('default_language', 'en', 'engine'),
    ('output_format', 'wav', 'audio'),
    ('sample_rate', '44100', 'audio'),
    ('use_gpu', 'true', 'engine'),
    ('theme', 'dark', 'ui');
"""


async def get_db() -> aiosqlite.Connection:
    """Get a database connection."""
    db = await aiosqlite.connect(str(settings.db_path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_database() -> None:
    """Initialize the database with schema."""
    settings.ensure_directories()
    async with aiosqlite.connect(str(settings.db_path)) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    print(f"Database initialized at {settings.db_path}")


async def close_db(db: aiosqlite.Connection) -> None:
    """Close a database connection."""
    await db.close()
