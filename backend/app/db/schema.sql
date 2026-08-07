-- AI Voice Clone Studio — Database schema
--
-- CONTRACT. Wave 0. B2 owns the access layer; this file owns the shape.
--
-- Connection policy (B2 implements): ONE long-lived aiosqlite connection,
-- PRAGMAs applied once at open, an asyncio.Lock around writes, handed out via
-- Depends(get_db). SQLite serializes writes anyway, so a pool buys nothing and
-- costs deadlocks. This replaces the per-call connect + repeated PRAGMAs, and
-- closes the connection leak at translation_service.py:164-178.
--
-- Note DATABASE.md documented column names that never existed (`voice_id`,
-- `text`, `file_path`). The real names are below; the doc is being rewritten.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;


-- ── Voice profiles: reference audio for cloning ──────────────────────────────

CREATE TABLE IF NOT EXISTS voice_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    audio_path      TEXT    NOT NULL,
    transcript      TEXT,
    language        TEXT    NOT NULL,
    duration_sec    REAL,
    sample_rate     INTEGER NOT NULL DEFAULT 44100,

    -- Reference quality dominates clone quality more than any model parameter,
    -- so it is measured once at upload and shown in the UI rather than
    -- rediscovered by the user after a bad generation.
    peak_dbfs       REAL,
    is_clipped      INTEGER NOT NULL DEFAULT 0,

    -- Sub-range of the file actually used as the reference. F5 hard-trims to
    -- ~6s; without this, WHICH 6s is arbitrary. NULL means "from the start".
    trim_start_sec  REAL,
    trim_end_sec    REAL,

    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- The profile list filters on is_active on every page load.
CREATE INDEX IF NOT EXISTS idx_voice_profiles_active
    ON voice_profiles (is_active);


-- ── Generation history ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS generation_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES voice_profiles(id) ON DELETE CASCADE,

    input_text      TEXT    NOT NULL,
    language        TEXT    NOT NULL,
    output_path     TEXT    NOT NULL,
    output_format   TEXT    NOT NULL DEFAULT 'wav',
    duration_sec    REAL,
    gen_time_sec    REAL,

    -- The route, denormalized on purpose. History must stay auditable after the
    -- catalog changes: when a model is removed, these rows still say exactly
    -- what produced them. A foreign key to a catalog table would either block
    -- removals or orphan the answer.
    model_id        TEXT    NOT NULL,
    transform       TEXT    NOT NULL DEFAULT 'none',
    is_lossy        INTEGER NOT NULL DEFAULT 0,
    source_script   TEXT    NOT NULL,
    route_rationale TEXT    NOT NULL,
    -- The post-transform string the model actually received. Differs from
    -- input_text whenever transform != 'none'; without it, a transliteration
    -- bug is undebuggable after the fact.
    resolved_text   TEXT,

    is_favorite     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- History is always read newest-first, paginated.
CREATE INDEX IF NOT EXISTS idx_history_created_at
    ON generation_history (created_at DESC);

-- "Show everything made with this voice."
CREATE INDEX IF NOT EXISTS idx_history_profile
    ON generation_history (profile_id);

-- Feeds the per-model usage stats and the est_load_sec feedback loop.
CREATE INDEX IF NOT EXISTS idx_history_model
    ON generation_history (model_id);


-- ── Model download state ─────────────────────────────────────────────────────
--
-- NOT a model registry. Which models EXIST is defined in code, in
-- inference/catalog.py, and nowhere else. This table records only the mutable
-- fact of what is on disk. The predecessor's model_registry duplicated the
-- catalog in the database, so the two drifted and neither was trustworthy.

CREATE TABLE IF NOT EXISTS model_downloads (
    model_id        TEXT    PRIMARY KEY,
    -- Pinned revision actually fetched. If this diverges from the catalog's
    -- pin, the local copy is stale and must be re-fetched: silently using
    -- whatever is on disk defeats the point of pinning.
    hf_revision     TEXT    NOT NULL,
    local_path      TEXT    NOT NULL,
    size_mb         INTEGER,
    downloaded_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at    TEXT
);


-- ── Settings: key/value ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL,
    category        TEXT    NOT NULL DEFAULT 'general',
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Note there is no 'default_engine' row. There is no default engine: routing
-- decides per request, from the declared language and detected script. The old
-- default_engine='mock' setting was never consulted during generation anyway —
-- it only ever surfaced in /api/system/status, which made it actively
-- misleading about what would actually run.
INSERT OR IGNORE INTO settings (key, value, category) VALUES
    ('default_language',  'en',    'generation'),
    ('urdu_strategy',     'native','generation'),
    ('output_format',     'wav',   'audio'),
    ('sample_rate',       '44100', 'audio'),
    ('theme',             'dark',  'ui');


-- ── Translation cache ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS translation_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text     TEXT    NOT NULL,
    source_lang     TEXT    NOT NULL,
    target_lang     TEXT    NOT NULL,
    translated_text TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_text, source_lang, target_lang)
);


-- ── Transliteration cache ────────────────────────────────────────────────────
--
-- Separate from translation_cache because it is a different operation with
-- different correctness properties: transliteration is script conversion within
-- one language, and arab->deva is lossy in a way roman->deva is not. Merging
-- them would make the lossy flag ambiguous.

CREATE TABLE IF NOT EXISTS transliteration_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text     TEXT    NOT NULL,
    transform       TEXT    NOT NULL,  -- TransformKind value
    result_text     TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_text, transform)
);
