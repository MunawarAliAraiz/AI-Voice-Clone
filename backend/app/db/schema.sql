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

    -- Short human label, 2-3 words, suggested by the analyzer and editable
    -- before generating. Nullable: every row written before 2026-08-16 has
    -- none, and the UI falls back to the text.
    title           TEXT,

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

    -- How many pause-joined segments Speech Direction rendered this into.
    -- 0 means the text was synthesized in one piece, with no direction applied.
    -- NULL means the row predates this column and we genuinely do not know --
    -- deliberately distinct from 0, because backfilling "no direction" onto
    -- rows that may well have used it would be inventing history.
    direction_segments INTEGER,

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


-- ── Jobs: the async queue ────────────────────────────────────────────────────
--
-- This table IS the queue. `app/inference/scheduler.py` is never given a FIFO
-- object of its own — position is a COUNT(*) over this table, computed in
-- `app/jobs/estimate.py`, which keeps the scheduler's eviction-under-slot
-- invariant untouched. See `app/jobs/__init__.py` for the full design.
--
-- There is a LIMITED migration mechanism as of 2026-08-16: `_ADDED_COLUMNS` in
-- database.py applies missing ADD COLUMNs on every connect, so a column added
-- here also reaches databases that already exist. It is ADD COLUMN ONLY — no
-- renames, drops, type changes or constraint changes, and no way to backfill.
-- So the rule below is relaxed, not repealed: a column can be added later, but
-- getting the SHAPE right still matters, because nothing here can change it.
-- CREATE TABLE IF NOT EXISTS still means an existing table keeps everything
-- else it has. Prefer having the column now, including result_json for kinds
-- that never produce a generation_history row.

CREATE TABLE IF NOT EXISTS jobs (
    -- AUTOINCREMENT doubles as the FIFO sequence: monotonic, so "how many jobs
    -- are ahead of me" is one COUNT with no extra column and no clock.
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,

    kind                   TEXT    NOT NULL,
    status                 TEXT    NOT NULL DEFAULT 'queued'
                           CHECK (status IN ('queued','running','succeeded','failed','cancelled')),

    -- Opaque outside the kind's own handler. For 'synthesize' this carries the
    -- resolved SynthRequest fields, INCLUDING output_path — see the orphan
    -- rule in app/jobs/runner.py: no file is ever written whose path is not
    -- already recorded in a job row.
    params_json            TEXT    NOT NULL,
    -- The RoutePlan, decided once by the pure resolve() at enqueue time and
    -- never recomputed at claim time. Re-routing toward whatever model happens
    -- to be resident when a job is claimed would be routing-by-residency —
    -- bit-for-bit the defect this codebase's rewrite exists to prevent.
    route_json             TEXT,

    -- Result refs. history_id is the typed ref for 'synthesize'. result_json
    -- exists for kinds that produce no history row (it cannot be added later).
    history_id             INTEGER REFERENCES generation_history(id) ON DELETE SET NULL,
    result_json            TEXT,

    -- RFC 9457, decomposed. Reassembled on read into exactly the document
    -- AppError.to_problem() produces, so the frontend's existing problem+json
    -- handling works on a job's error field unchanged. Raw exception text is
    -- NEVER stored here — a torch stack trace or filesystem path must not
    -- reach a client; unexpected exceptions log server-side and store
    -- error_code='INTERNAL_ERROR' with a generic detail.
    error_code             TEXT,
    error_title            TEXT,
    error_status           INTEGER,
    error_detail           TEXT,
    error_extensions_json  TEXT,

    profile_id             INTEGER REFERENCES voice_profiles(id) ON DELETE CASCADE,
    priority               INTEGER NOT NULL DEFAULT 0,
    -- The failed job this row is a retry of, so the UI can tell an
    -- un-retried failure from one whose retry is already queued.
    retry_of_job_id        INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    cancel_requested       INTEGER NOT NULL DEFAULT 0,
    attempt                INTEGER NOT NULL DEFAULT 0,

    queued_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    started_at             TEXT,
    finished_at            TEXT,
    updated_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- The claim query: WHERE status='queued' AND kind=? ORDER BY priority DESC, id.
CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs (status, kind, id);

-- The "Recent" view: newest-first, paginated.
CREATE INDEX IF NOT EXISTS idx_jobs_queued
    ON jobs (queued_at DESC);

-- "Show everything queued for this voice."
CREATE INDEX IF NOT EXISTS idx_jobs_profile
    ON jobs (profile_id);


-- ── Pronunciation dictionary: the user's own respellings ─────────────────────
--
-- OmniVoice mispronounces some words, and the fix is to rewrite the text before
-- synthesis. `domain/urdu_text.py`'s DEFAULT_LOANWORD_LEXICON holds the handful
-- verified by ear by the maintainer; this table holds the user's own, which the
-- maintainer cannot verify and should not try to.
--
-- Rows are DATA for the pure `apply_text_normalizations()`, which takes the
-- lexicon as an argument. Nothing in `domain/` reads this table — golden rule 4
-- keeps routing pure, and a pure function may receive data but not fetch it.
-- The enqueue path loads these rows and passes them down.
--
-- Same constraint as `jobs` above: a missing column can be added later via
-- `_ADDED_COLUMNS`, but nothing else about an existing table can change. So
-- every column this will foreseeably need exists now -- `language` and `notes`
-- are here for that reason rather than because anything reads them yet.

CREATE TABLE IF NOT EXISTS pronunciation_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- The word as the user types it, in EITHER script. Latin for an English
    -- loanword sitting inside Urdu ('database'); Perso-Arabic for a word the
    -- transliterator already converted ('میٹنگ', which OmniVoice reads as
    -- "mating"). A Latin-only key could not reach the second case at all.
    key_text        TEXT    NOT NULL,
    -- What to synthesize instead. Used verbatim: no case is carried over,
    -- because the replacement is usually Perso-Arabic, which has none.
    replacement     TEXT    NOT NULL,

    -- Which language's text this applies to. Only 'ur' is used today; the
    -- column exists because it cannot be added later.
    language        TEXT    NOT NULL DEFAULT 'ur',

    -- Lets a user switch an entry off without losing what they wrote — and,
    -- when key_text matches a shipped default, SUPPRESSES that default. That
    -- is the only way to turn off a built-in entry, so this is load-bearing,
    -- not a convenience.
    is_enabled      INTEGER NOT NULL DEFAULT 1,

    -- Free text, e.g. "was read as 'mating'". Never interpreted.
    notes           TEXT,

    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    -- COLLATE NOCASE mirrors the matcher, which is case-insensitive so a user
    -- writes an entry once and then types whatever case they like. SQLite's
    -- NOCASE folds ASCII A-Z only, which is exactly the range where case
    -- exists here: Perso-Arabic is caseless, so there is no divergence between
    -- this constraint and Python's `str.casefold()` for any realistic entry.
    UNIQUE (language, key_text COLLATE NOCASE)
);

-- Every synthesis loads this user's enabled entries for one language.
CREATE INDEX IF NOT EXISTS idx_pronunciation_lookup
    ON pronunciation_entries (language, is_enabled);
