"""
AI Voice Clone Studio — Database access layer.

Implements the connection policy frozen in `schema.sql`: ONE long-lived
aiosqlite connection, PRAGMAs applied once at open (they live in the schema
file), and an `asyncio.Lock` around writes because SQLite serializes writes
anyway — a pool buys nothing here and costs deadlocks.

Rows come back as `aiosqlite.Row` (mapping access). The route on a generation is
stored denormalized so history stays auditable after the catalog changes.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite

__all__ = ["Database"]

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # Set explicitly too: foreign_keys is per-connection and some drivers do
        # not carry a PRAGMA from executescript onto the live connection, which
        # would silently disable the ON DELETE CASCADE from a profile to its
        # generations.
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await self._conn.commit()
        # The job queue's atomic claim (UPDATE ... RETURNING) needs SQLite 3.35+
        # (2021). Assert it here rather than discovering it at the first claim,
        # deep inside a request.
        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise RuntimeError(
                f"SQLite {sqlite3.sqlite_version} is too old for the job queue's "
                f"atomic claim (UPDATE ... RETURNING needs 3.35+)."
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _c(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    # ── voice profiles ───────────────────────────────────────────────────────

    async def create_profile(
        self, *, name: str, audio_path: str | Path, language: str,
        transcript: str | None, duration_sec: float | None, sample_rate: int,
        peak_dbfs: float | None, is_clipped: bool,
    ) -> aiosqlite.Row:
        async with self._write_lock:
            cur = await self._c.execute(
                """INSERT INTO voice_profiles
                   (name, audio_path, language, transcript, duration_sec,
                    sample_rate, peak_dbfs, is_clipped)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, str(audio_path), language, transcript, duration_sec,
                 sample_rate, peak_dbfs, int(is_clipped)),
            )
            await self._c.commit()
            new_id = cur.lastrowid
        row = await self.get_profile(int(new_id))
        assert row is not None
        return row

    async def get_profile(self, profile_id: int) -> aiosqlite.Row | None:
        cur = await self._c.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        )
        return await cur.fetchone()

    async def get_profiles_by_ids(self, profile_ids: Sequence[int]) -> dict[int, aiosqlite.Row]:
        """
        Batched `get_profile`, keyed by id. Exists so a list endpoint (history,
        jobs) can resolve N rows' `profile_name` in one query instead of N —
        `routers/history.py` used to call `get_profile` once per row on every
        page load.
        """
        unique = sorted(set(profile_ids))
        if not unique:
            return {}
        placeholders = ",".join("?" for _ in unique)
        cur = await self._c.execute(
            f"SELECT * FROM voice_profiles WHERE id IN ({placeholders})",  # noqa: S608
            unique,
        )
        rows = await cur.fetchall()
        return {row["id"]: row for row in rows}

    async def list_profiles(self, *, active_only: bool = True) -> list[aiosqlite.Row]:
        q = "SELECT * FROM voice_profiles"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY created_at DESC, id DESC"
        cur = await self._c.execute(q)
        return list(await cur.fetchall())

    async def update_profile(self, profile_id: int, **fields: Any) -> aiosqlite.Row | None:
        allowed = {"name", "transcript", "is_active"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not sets:
            return await self.get_profile(profile_id)
        # `cols` is built only from the `allowed` whitelist above — never from
        # user input — so the interpolation is safe; values stay parameterized.
        cols = ", ".join(f"{k} = ?" for k in sets)
        async with self._write_lock:
            await self._c.execute(
                f"UPDATE voice_profiles SET {cols}, updated_at = datetime('now') WHERE id = ?",  # noqa: S608
                (*sets.values(), profile_id),
            )
            await self._c.commit()
        return await self.get_profile(profile_id)

    async def delete_profile(self, profile_id: int) -> bool:
        async with self._write_lock:
            cur = await self._c.execute(
                "DELETE FROM voice_profiles WHERE id = ?", (profile_id,)
            )
            await self._c.commit()
            return cur.rowcount > 0

    async def count_profiles(self) -> int:
        cur = await self._c.execute("SELECT COUNT(*) AS n FROM voice_profiles")
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # ── generation history ───────────────────────────────────────────────────

    async def create_generation(
        self, *, profile_id: int, input_text: str, language: str,
        output_path: str | Path, output_format: str, duration_sec: float | None,
        gen_time_sec: float | None, model_id: str, transform: str, is_lossy: bool,
        source_script: str, route_rationale: str, resolved_text: str | None,
    ) -> aiosqlite.Row:
        async with self._write_lock:
            cur = await self._c.execute(
                """INSERT INTO generation_history
                   (profile_id, input_text, language, output_path, output_format,
                    duration_sec, gen_time_sec, model_id, transform, is_lossy,
                    source_script, route_rationale, resolved_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (profile_id, input_text, language, str(output_path), output_format,
                 duration_sec, gen_time_sec, model_id, transform, int(is_lossy),
                 source_script, route_rationale, resolved_text),
            )
            await self._c.commit()
            new_id = cur.lastrowid
        row = await self.get_generation(int(new_id))
        assert row is not None
        return row

    async def get_generation(self, gen_id: int) -> aiosqlite.Row | None:
        cur = await self._c.execute(
            "SELECT * FROM generation_history WHERE id = ?", (gen_id,)
        )
        return await cur.fetchone()

    async def find_generation_by_output_path(self, output_path: str) -> aiosqlite.Row | None:
        """
        Used only by the job-queue startup reaper's orphan check. Deliberately
        queries the source of truth (does ANY history row reference this exact
        file) rather than trusting a job row's own `history_id` link — a job
        can finish its work (audio written, history row created) and still be
        cancelled at shutdown in the narrow window before its own row is
        updated to 'succeeded'. Trusting `job.history_id is None` there would
        delete a file a real history row still points at.
        """
        cur = await self._c.execute(
            "SELECT * FROM generation_history WHERE output_path = ? LIMIT 1", (output_path,)
        )
        return await cur.fetchone()

    async def list_generations(
        self, *, limit: int = 50, offset: int = 0, profile_id: int | None = None
    ) -> list[aiosqlite.Row]:
        q = "SELECT * FROM generation_history"
        params: list[Any] = []
        if profile_id is not None:
            q += " WHERE profile_id = ?"
            params.append(profile_id)
        q += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        cur = await self._c.execute(q, params)
        return list(await cur.fetchall())

    async def set_favorite(self, gen_id: int, is_favorite: bool) -> aiosqlite.Row | None:
        async with self._write_lock:
            await self._c.execute(
                "UPDATE generation_history SET is_favorite = ? WHERE id = ?",
                (int(is_favorite), gen_id),
            )
            await self._c.commit()
        return await self.get_generation(gen_id)

    async def delete_generation(self, gen_id: int) -> bool:
        async with self._write_lock:
            cur = await self._c.execute(
                "DELETE FROM generation_history WHERE id = ?", (gen_id,)
            )
            await self._c.commit()
            return cur.rowcount > 0

    async def count_generations(self) -> int:
        cur = await self._c.execute("SELECT COUNT(*) AS n FROM generation_history")
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # ── jobs (async queue) ───────────────────────────────────────────────────
    #
    # Rows come back raw, same as everywhere else in this file — decoding
    # params_json/route_json/result_json into a JobRecord is the jobs
    # package's job (see app/jobs/runner.py), not this layer's.

    async def create_job(
        self, *, kind: str, params_json: str, route_json: str | None,
        profile_id: int | None, priority: int = 0,
    ) -> aiosqlite.Row:
        async with self._write_lock:
            cur = await self._c.execute(
                """INSERT INTO jobs (kind, params_json, route_json, profile_id, priority)
                   VALUES (?, ?, ?, ?, ?)""",
                (kind, params_json, route_json, profile_id, priority),
            )
            await self._c.commit()
            new_id = cur.lastrowid
        row = await self.get_job(int(new_id))
        assert row is not None
        return row

    async def get_job(self, job_id: int) -> aiosqlite.Row | None:
        cur = await self._c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return await cur.fetchone()

    async def list_jobs(
        self, *, limit: int = 50, offset: int = 0, kind: str | None = None,
        profile_id: int | None = None,
    ) -> list[aiosqlite.Row]:
        q = "SELECT * FROM jobs"
        where: list[str] = []
        params: list[Any] = []
        if kind is not None:
            where.append("kind = ?")
            params.append(kind)
        if profile_id is not None:
            where.append("profile_id = ?")
            params.append(profile_id)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY queued_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        cur = await self._c.execute(q, params)
        return list(await cur.fetchall())

    async def count_jobs(self, *, kind: str | None = None) -> int:
        q = "SELECT COUNT(*) AS n FROM jobs"
        params: list[Any] = []
        if kind is not None:
            q += " WHERE kind = ?"
            params.append(kind)
        cur = await self._c.execute(q, params)
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def claim_next_job(self, kind: str) -> aiosqlite.Row | None:
        """
        Atomically move the oldest eligible 'queued' job of `kind` to
        'running' and return it — one statement, one write-lock acquisition,
        no window in which two pool tasks could observe the same job as
        claimable.
        """
        async with self._write_lock:
            cur = await self._c.execute(
                """UPDATE jobs
                      SET status = 'running',
                          started_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                          updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                          attempt = attempt + 1
                    WHERE id = (
                        SELECT id FROM jobs
                         WHERE status = 'queued' AND kind = ? AND cancel_requested = 0
                         ORDER BY priority DESC, id
                         LIMIT 1
                    )
                    RETURNING *""",
                (kind,),
            )
            row = await cur.fetchone()
            await self._c.commit()
            return row

    async def finish_job(
        self, job_id: int, *, history_id: int | None, result_json: str | None,
    ) -> aiosqlite.Row | None:
        async with self._write_lock:
            await self._c.execute(
                """UPDATE jobs
                      SET status = 'succeeded',
                          history_id = ?,
                          result_json = ?,
                          finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                          updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    WHERE id = ?""",
                (history_id, result_json, job_id),
            )
            await self._c.commit()
        return await self.get_job(job_id)

    async def fail_job(
        self, job_id: int, *, error_code: str, error_title: str, error_status: int,
        error_detail: str, error_extensions_json: str,
    ) -> aiosqlite.Row | None:
        async with self._write_lock:
            await self._c.execute(
                """UPDATE jobs
                      SET status = 'failed',
                          error_code = ?, error_title = ?, error_status = ?,
                          error_detail = ?, error_extensions_json = ?,
                          finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                          updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    WHERE id = ?""",
                (error_code, error_title, error_status, error_detail,
                 error_extensions_json, job_id),
            )
            await self._c.commit()
        return await self.get_job(job_id)

    async def cancel_queued_job(self, job_id: int) -> aiosqlite.Row | None:
        """
        Cancel only if still 'queued'. Atomic: if the pool claimed it between
        the caller's status check and this call, zero rows match and None
        comes back — the caller re-reads the job to build the right error
        (already terminal vs. now running, i.e. `JobNotCancellableError`).
        """
        async with self._write_lock:
            cur = await self._c.execute(
                """UPDATE jobs
                      SET status = 'cancelled', cancel_requested = 1,
                          finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                          updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    WHERE id = ? AND status = 'queued'
                    RETURNING *""",
                (job_id,),
            )
            row = await cur.fetchone()
            await self._c.commit()
            return row

    async def count_pending(self, kind: str) -> int:
        """Jobs of `kind` not yet terminal — the enqueue-time backpressure bound."""
        cur = await self._c.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE kind = ? AND status IN ('queued','running')",
            (kind,),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def count_ahead(self, job_id: int, kind: str) -> int:
        """How many still-queued jobs of `kind` were enqueued before this one."""
        cur = await self._c.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE kind = ? AND status = 'queued' AND id < ?",
            (kind, job_id),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def list_active_jobs(self, kind: str) -> list[aiosqlite.Row]:
        """
        Every not-yet-terminal job of `kind`, in enqueue order. Feeds
        `app/jobs/estimate.py`'s ETA math: at most one row is 'running' (it
        can appear anywhere in this id-ordered list — claim order isn't
        enqueue order — the caller splits by status, not position).
        """
        cur = await self._c.execute(
            "SELECT * FROM jobs WHERE kind = ? AND status IN ('queued','running') ORDER BY id",
            (kind,),
        )
        return list(await cur.fetchall())

    async def has_running(self, kind: str) -> bool:
        cur = await self._c.execute(
            "SELECT 1 FROM jobs WHERE kind = ? AND status = 'running' LIMIT 1", (kind,)
        )
        return await cur.fetchone() is not None

    async def reap_running(
        self, *, error_code: str, error_title: str, error_status: int, error_detail: str,
    ) -> list[aiosqlite.Row]:
        """
        Fail every 'running' row and return the PRE-update rows (so the caller
        can read `params_json` for the output path and decide whether to
        unlink an orphaned file). Dead by definition: this app runs one
        uvicorn worker, so a 'running' row at startup can only be from the
        process that just died.
        """
        async with self._write_lock:
            cur = await self._c.execute("SELECT * FROM jobs WHERE status = 'running'")
            rows = list(await cur.fetchall())
            if rows:
                await self._c.execute(
                    """UPDATE jobs
                          SET status = 'failed',
                              error_code = ?, error_title = ?, error_status = ?,
                              error_detail = ?, error_extensions_json = '{}',
                              finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                              updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                        WHERE status = 'running'""",
                    (error_code, error_title, error_status, error_detail),
                )
                await self._c.commit()
            return rows

    async def expire_queued(
        self, *, max_age_sec: int, error_code: str, error_title: str,
        error_status: int, error_detail: str,
    ) -> int:
        """Fail 'queued' rows older than `max_age_sec`. Returns count expired."""
        async with self._write_lock:
            cur = await self._c.execute(
                """UPDATE jobs
                      SET status = 'failed',
                          error_code = ?, error_title = ?, error_status = ?,
                          error_detail = ?, error_extensions_json = '{}',
                          finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                          updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    WHERE status = 'queued'
                      AND queued_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' seconds')""",
                (error_code, error_title, error_status, error_detail, f"-{max_age_sec}"),
            )
            await self._c.commit()
            return cur.rowcount

    async def delete_jobs_older_than(self, *, retention_hours: int) -> int:
        """Delete terminal jobs whose `finished_at` is older than the retention window."""
        async with self._write_lock:
            cur = await self._c.execute(
                """DELETE FROM jobs
                    WHERE status IN ('succeeded','failed','cancelled')
                      AND finished_at IS NOT NULL
                      AND finished_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' hours')""",
                (f"-{retention_hours}",),
            )
            await self._c.commit()
            return cur.rowcount

    # ── pronunciation dictionary ─────────────────────────────────────────────
    #
    # These return ROWS, not a lexicon. Merging the user's entries over the
    # shipped defaults is policy, and policy lives in `domain/urdu_text.py`'s
    # pure `effective_lexicon()` where it can be tested without a database.

    async def list_pronunciations(
        self, *, language: str | None = None, enabled_only: bool = False
    ) -> list[aiosqlite.Row]:
        """
        Newest first, so the settings list shows what was just added at the top.

        `enabled_only` is for the synthesis path, which wants only rows that
        actually apply. The settings UI wants everything, disabled included —
        an entry the user switched off must still be visible to switch back on.
        """
        # Static SQL with the filters expressed as parameters, rather than a
        # WHERE clause assembled in Python. Both filters are internal, so an
        # assembled clause would also be safe, but this one cannot drift into
        # being unsafe later and needs no `noqa` to say so.
        cur = await self._c.execute(
            """SELECT * FROM pronunciation_entries
                WHERE (? IS NULL OR language = ?)
                  AND (? = 0 OR is_enabled = 1)
                ORDER BY id DESC""",
            (language, language, int(enabled_only)),
        )
        return list(await cur.fetchall())

    async def get_pronunciation(self, entry_id: int) -> aiosqlite.Row | None:
        cur = await self._c.execute(
            "SELECT * FROM pronunciation_entries WHERE id = ?", (entry_id,)
        )
        return await cur.fetchone()

    async def find_pronunciation(self, *, key_text: str, language: str) -> aiosqlite.Row | None:
        """
        Case-insensitive lookup by key, matching the UNIQUE constraint.

        Exists so the API can answer "this word already has an entry" with the
        conflicting row rather than only a constraint violation — a user who
        re-adds `database` should be shown what they already wrote.
        """
        cur = await self._c.execute(
            """SELECT * FROM pronunciation_entries
                WHERE language = ? AND key_text = ? COLLATE NOCASE""",
            (language, key_text),
        )
        return await cur.fetchone()

    async def create_pronunciation(
        self, *, key_text: str, replacement: str, language: str = "ur",
        is_enabled: bool = True, notes: str | None = None,
    ) -> aiosqlite.Row:
        """
        Raises `sqlite3.IntegrityError` on a duplicate key for the language.
        Deliberately not caught here: the access layer reports what the database
        said, and the API layer decides that this means 409.
        """
        async with self._write_lock:
            cur = await self._c.execute(
                """INSERT INTO pronunciation_entries
                   (key_text, replacement, language, is_enabled, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (key_text, replacement, language, int(is_enabled), notes),
            )
            await self._c.commit()
            new_id = int(cur.lastrowid)
        row = await self.get_pronunciation(new_id)
        assert row is not None
        return row

    async def update_pronunciation(
        self, entry_id: int, **fields: Any
    ) -> aiosqlite.Row | None:
        """
        Partial update. `None` for a field means "not supplied", so an entry's
        `notes` cannot be cleared by passing None — pass "" instead. Returns
        None when the row does not exist.
        """
        allowed = {"key_text", "replacement", "language", "is_enabled", "notes"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "is_enabled" in sets:
            sets["is_enabled"] = int(sets["is_enabled"])
        if not sets:
            return await self.get_pronunciation(entry_id)
        # `cols` is built only from the `allowed` whitelist above — never from
        # user input — so the interpolation is safe; values stay parameterized.
        cols = ", ".join(f"{k} = ?" for k in sets)
        async with self._write_lock:
            await self._c.execute(
                f"UPDATE pronunciation_entries SET {cols},"  # noqa: S608
                " updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (*sets.values(), entry_id),
            )
            await self._c.commit()
        return await self.get_pronunciation(entry_id)

    async def delete_pronunciation(self, entry_id: int) -> bool:
        async with self._write_lock:
            cur = await self._c.execute(
                "DELETE FROM pronunciation_entries WHERE id = ?", (entry_id,)
            )
            await self._c.commit()
            return cur.rowcount > 0
