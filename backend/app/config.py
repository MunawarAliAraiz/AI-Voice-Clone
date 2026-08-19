"""
AI Voice Clone Studio — Configuration.

Replaces the predecessor config, whose `default_engine = "mock"` is the exact
default this rewrite exists to remove. No engine default, no `use_gpu` (the API
process has no GPU), and the scheduler budget lives here rather than being
guessed at call time.

Every setting is overridable by a `VCS_`-prefixed env var (e.g. `VCS_API_KEY`).
"""

from __future__ import annotations

import secrets
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .inference.spec import RuntimeKind

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VCS_", env_file=".env", extra="ignore"
    )

    version: str = "0.1.0"

    #: When non-empty, every `/api/*` route except health requires this in the
    #: `X-API-Key` header. Empty = open (single-user local dev only).
    api_key: str = ""

    #: Enables the silence-only fake runtime. A response so produced carries
    #: `X-Fake-Audio: true` and the UI shows a loud banner — never a silent path.
    allow_fake_runtime: bool = False

    #: CORS allow-list. Set `VCS_CORS_ORIGINS` to a JSON array to override.
    #: 1420 is the Vite dev port from vite.config; 5173 is Vite's default.
    cors_origins: list[str] = [
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    #: Root for all mutable state — reference audio, generated clips, the db.
    data_dir: Path = Path("data")

    #: HMAC secret for signed media URLs. Auto-generated per process if unset, so
    #: dev works out of the box; set it in production so tokens survive restarts.
    media_token_secret: str = ""
    media_token_ttl_sec: int = 3600

    #: Scheduler capacity. Defaults suit a 24 GB card; re-derive for other GPUs.
    budget_mb: int = 16_000
    max_workers: int = 2

    #: Absolute path to each runtime's venv python. A runtime with no interpreter
    #: is simply unrunnable — the factory raises rather than guessing one.
    voxcpm_python: str = ""
    chatterbox_python: str = ""
    f5_python: str = ""
    omnivoice_python: str = ""
    fake_python: str = ""
    #: Absolute path to the Qwen Speech-Direction analyzer's venv python. NOT
    #: part of `interpreters()`/`RuntimeKind` — that mechanism is specifically
    #: for audio `ModelSpec` routing (`make_worker_factory`), and this
    #: analyzer must never be reachable from `domain/routing.py`'s
    #: `resolve()`. `AnalyzerScheduler` reads this directly.
    qwen_analyzer_python: str = ""
    #: Seconds of no `classify()` calls before `AnalyzerScheduler` kills its
    #: worker subprocess to release VRAM. See `analyzer_scheduler.py`'s
    #: docstring for why this exists (the audio scheduler's `budget_mb` is
    #: sized assuming only audio models are resident).
    qwen_analyzer_idle_unload_sec: float = 300.0
    #: What the analyzer occupies while resident, for `check_capacity`'s
    #: arithmetic. Qwen2.5-3B, ~6 GB. Sits OUTSIDE `budget_mb` because it is
    #: not a `RuntimeKind` — a routing decision with a VRAM consequence that
    #: went unaccounted until the transliterator forced the sums to be written
    #: down.
    qwen_analyzer_reserve_mb: int = 6000
    #: Interpreter for the Gemma transliterator worker. Same reasoning as the
    #: analyzer above and the same deliberate omission from `interpreters()`:
    #: converting Roman/Devanagari text to Perso-Arabic is not audio, and
    #: `resolve()` must never be able to route a TTS request to it.
    #: `TransliteratorScheduler` reads this directly. Unset means the Convert
    #: action fails with a clear error naming this variable; it never breaks
    #: generation.
    gemma_transliterator_python: str = ""
    #: What Gemma occupies while resident. **MEASURED, not estimated**:
    #: `scripts/phase_b_smoke.py` recorded a 19221 MiB peak across four
    #: conversions on an A40. `check_capacity` adds this to `budget_mb` and the
    #: analyzer's reserve; when the total exceeds the card, script conversion
    #: is DISABLED WITH A REASON and everything else still runs.
    gemma_transliterator_reserve_mb: int = 19500
    #: Seconds of no conversions before the ~19 GB worker is killed.
    #:
    #: 30 minutes against the analyzer's 5, and the ratio is a measurement:
    #: that one reloads in seconds, this one in 150-330 s. Reclaiming VRAM
    #: should not cost five minutes to undo because somebody paused to read.
    gemma_transliterator_idle_unload_sec: float = 1800.0

    #: Chunk sizing for an imported transcript.
    #:
    #: A CONFIG SETTING, NOT A PER-MODEL FIGURE, and the difference is worth
    #: stating: `chunk_for_synthesis`'s docstring is explicit that `max_chars`
    #: derives from a model's frame limit and must never be hardcoded in shared
    #: code. `ModelSpec` carries no such field today and inventing one would
    #: mean inventing numbers for four runtimes that have never been measured.
    #: So this is a conservative default the owner can tune, and the honest
    #: upgrade path is a measured `ModelSpec.max_chars` — not a guess dressed
    #: up as one.
    transcript_chunk_chars: int = 600
    #: Floor for the FINAL chunk. `chunk_for_synthesis` uses it to avoid
    #: emitting a two-word tail, which every one of these models renders with
    #: audibly clipped prosody.
    transcript_chunk_min_chars: int = 80
    #: Hard ceiling on a pasted script before chunking. Refusing early beats
    #: chunking a novel into thousands of pieces the UI then has to render.
    transcript_max_chars: int = 200_000
    #: Directory workers start in (must contain the importable `app` package).
    worker_cwd: Path | None = None

    #: Model id to start loading as soon as the app boots, so the first real
    #: /generate doesn't pay the ~20-60s cold-load cost. Fired in the background
    #: from the lifespan — startup and /api/health return immediately either way,
    #: this only changes how soon a worker happens to already be resident.
    #: COMMA-SEPARATED. A single id still works, so no existing deployment
    #: breaks; `voxcpm2,omnivoice_urdu` warms both. VoxCPM2 7300 MB +
    #: OmniVoice 4700 MB = 12 GB, inside `budget_mb = 16000` with
    #: `max_workers = 2`, so both stay resident rather than evicting each other.
    warm_on_startup: str | None = None

    #: After a model's weights load, run ONE short synthesis and delete the
    #: output. This exists because OmniVoice lazily loads an embedded Whisper
    #: sub-model on its FIRST `synth()` call when no `ref_text` is given
    #: (measured in the Phase 1 pod smoke test), so warming weights alone still
    #: leaves the first real generation paying tens of seconds. Needs a voice
    #: profile to use as the reference; skipped with a log line when there are
    #: none, because a warm-up cannot invent one.
    warm_synth_on_startup: bool = True

    @property
    def warm_model_ids(self) -> tuple[str, ...]:
        raw = self.warm_on_startup or ""
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    max_upload_mb: int = 50
    default_sample_rate: int = 44_100

    #: Async job queue (see `app/jobs/`). `job_concurrency` is per JobKind — the
    #: default of 1 for SYNTHESIZE matches the scheduler's single GPU slot; a
    #: second concurrent synth task would just block holding an admission
    #: permit while making eviction thrash between two models possible.
    job_concurrency: int = 1
    #: Queue-depth backpressure at enqueue time (not the scheduler's in-memory
    #: admission semaphore, which stays uncontended once the pool is the only
    #: caller of synthesize()). A much larger, durable bound than "8 concurrent
    #: HTTP requests" — see JobQueueFullError.
    job_queue_limit: int = 32
    #: How long the lifespan waits for an in-flight job before cancelling the
    #: pool tasks at shutdown. Long enough for a warm 2-10s synth; short enough
    #: not to hang a deploy behind a 124s cold load.
    job_drain_timeout_sec: float = 30.0
    #: A 'queued' job found at startup older than this is expired rather than
    #: run, so a crashed pod doesn't synthesize a pile of stale clips on boot.
    job_queue_max_age_sec: int = 3600
    #: Terminal jobs are deleted at startup once older than this.
    job_retention_hours: int = 72

    @model_validator(mode="after")
    def _default_media_secret(self) -> Settings:
        if not self.media_token_secret:
            self.media_token_secret = secrets.token_hex(32)
        return self

    @model_validator(mode="after")
    def _refuse_wildcard_cors_behind_a_key(self) -> Settings:
        """
        `VCS_CORS_ORIGINS=["*"]` together with an API key is refused at boot.

        Setting a key means the deployment is reachable by someone other than
        you, and a wildcard then lets any site on the internet drive it from a
        victim's browser. It is also worse than it looks: the CORS middleware
        runs with `allow_credentials`, and Starlette answers a wildcard+
        credentials config by echoing back whatever Origin asked — so every
        origin is allowed, individually.

        This check was documented in the README long before it existed. Name the
        origins instead.
        """
        if self.api_key and "*" in self.cors_origins:
            raise ValueError(
                "VCS_CORS_ORIGINS='*' is not allowed together with VCS_API_KEY. "
                "List the exact origins that may call this API, e.g. "
                '\'["https://studio.example.com"]\'.'
            )
        return self

    # ── derived paths ────────────────────────────────────────────────────────

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def generated_dir(self) -> Path:
        return self.data_dir / "generated"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "voiceclone.db"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.voices_dir, self.generated_dir):
            d.mkdir(parents=True, exist_ok=True)

    def interpreters(self) -> dict[RuntimeKind, str]:
        """RuntimeKind -> venv python, for `make_worker_factory`."""
        m: dict[RuntimeKind, str] = {}
        if self.voxcpm_python:
            m[RuntimeKind.VOXCPM] = self.voxcpm_python
        if self.chatterbox_python:
            m[RuntimeKind.CHATTERBOX] = self.chatterbox_python
        if self.f5_python:
            m[RuntimeKind.F5] = self.f5_python
        if self.omnivoice_python:
            m[RuntimeKind.OMNIVOICE] = self.omnivoice_python
        if self.allow_fake_runtime:
            m[RuntimeKind.FAKE] = self.fake_python or sys.executable
        return m


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton. Cached so the media secret is stable."""
    return Settings()
