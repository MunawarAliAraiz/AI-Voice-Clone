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
