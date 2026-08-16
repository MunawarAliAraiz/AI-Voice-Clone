"""Database access-layer tests against an in-memory SQLite (no files, no GPU)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import Database


async def _db() -> Database:
    db = Database(":memory:")
    await db.connect()
    return db


async def test_profile_crud() -> None:
    db = await _db()
    try:
        p = await db.create_profile(
            name="My voice", audio_path="/x/a.wav", language="ur", transcript=None,
            duration_sec=6.5, sample_rate=24000, peak_dbfs=-3.0, is_clipped=False,
        )
        assert p["id"] and p["name"] == "My voice" and p["language"] == "ur"
        assert p["is_active"] == 1
        assert (await db.get_profile(p["id"]))["audio_path"] == "/x/a.wav"
        assert len(await db.list_profiles()) == 1
        assert await db.count_profiles() == 1

        renamed = await db.update_profile(p["id"], name="Renamed")
        assert renamed is not None and renamed["name"] == "Renamed"

        assert await db.delete_profile(p["id"]) is True
        assert await db.get_profile(p["id"]) is None
        assert await db.delete_profile(p["id"]) is False  # already gone
    finally:
        await db.close()


async def test_generation_records_route_and_cascades() -> None:
    db = await _db()
    try:
        p = await db.create_profile(
            name="v", audio_path="/x/a.wav", language="en", transcript=None,
            duration_sec=5.0, sample_rate=44100, peak_dbfs=-1.0, is_clipped=False,
        )
        g = await db.create_generation(
            profile_id=p["id"], input_text="hello", language="en",
            output_path="/x/out.wav", output_format="wav", duration_sec=1.5,
            gen_time_sec=0.8, model_id="voxcpm2", transform="none", is_lossy=False,
            source_script="latin", route_rationale="en in latin", resolved_text="hello",
        )
        assert g["model_id"] == "voxcpm2" and g["transform"] == "none"
        assert (await db.list_generations())[0]["input_text"] == "hello"
        assert await db.count_generations() == 1

        # ON DELETE CASCADE: removing the profile removes its generations.
        await db.delete_profile(p["id"])
        assert await db.count_generations() == 0
    finally:
        await db.close()


# ── pronunciation dictionary ─────────────────────────────────────────────────


async def test_pronunciation_crud() -> None:
    db = await _db()
    try:
        e = await db.create_pronunciation(
            key_text="database", replacement="ڈیٹا بےس", notes="read as data-boss"
        )
        assert e["id"] and e["language"] == "ur" and e["is_enabled"] == 1
        assert e["notes"] == "read as data-boss"

        assert (await db.get_pronunciation(e["id"]))["replacement"] == "ڈیٹا بےس"
        assert len(await db.list_pronunciations()) == 1

        updated = await db.update_pronunciation(e["id"], replacement="ڈیٹا بیس")
        assert updated is not None and updated["replacement"] == "ڈیٹا بیس"
        assert updated["key_text"] == "database", "an unsupplied field must not change"

        assert await db.delete_pronunciation(e["id"]) is True
        assert await db.get_pronunciation(e["id"]) is None
        assert await db.delete_pronunciation(e["id"]) is False
    finally:
        await db.close()


async def test_pronunciation_keys_may_be_perso_arabic() -> None:
    """A3's میٹنگ case — the key arrives already converted, not as Latin."""
    db = await _db()
    try:
        e = await db.create_pronunciation(key_text="میٹنگ", replacement="مِیٹِنگ")
        assert (await db.find_pronunciation(key_text="میٹنگ", language="ur"))["id"] == e["id"]
    finally:
        await db.close()


async def test_pronunciation_key_is_unique_per_language_case_insensitively() -> None:
    """
    Mirrors the matcher, which is case-insensitive. Two rows differing only in
    case would make which one applies depend on alternation order.
    """
    db = await _db()
    try:
        await db.create_pronunciation(key_text="database", replacement="ڈیٹا بےس")
        with pytest.raises(sqlite3.IntegrityError):
            await db.create_pronunciation(key_text="DataBase", replacement="something")

        # Same key, different language, is a different entry.
        other = await db.create_pronunciation(
            key_text="database", replacement="day-ta-base", language="en"
        )
        assert other["id"]
    finally:
        await db.close()


async def test_find_pronunciation_is_case_insensitive() -> None:
    db = await _db()
    try:
        await db.create_pronunciation(key_text="database", replacement="ڈیٹا بےس")
        assert await db.find_pronunciation(key_text="DATABASE", language="ur") is not None
        assert await db.find_pronunciation(key_text="database", language="en") is None
    finally:
        await db.close()


async def test_list_pronunciations_filters() -> None:
    db = await _db()
    try:
        await db.create_pronunciation(key_text="database", replacement="ڈیٹا بےس")
        off = await db.create_pronunciation(
            key_text="URL", replacement="یو آر ایل", is_enabled=False
        )
        await db.create_pronunciation(
            key_text="schedule", replacement="sked-yool", language="en"
        )

        assert len(await db.list_pronunciations()) == 3
        assert len(await db.list_pronunciations(language="ur")) == 2
        assert len(await db.list_pronunciations(language="ur", enabled_only=True)) == 1

        # The settings UI must still see a disabled row — it is the only way to
        # switch it back on.
        assert off["id"] in {r["id"] for r in await db.list_pronunciations(language="ur")}
    finally:
        await db.close()


async def test_list_pronunciations_is_newest_first() -> None:
    db = await _db()
    try:
        await db.create_pronunciation(key_text="database", replacement="a")
        second = await db.create_pronunciation(key_text="URL", replacement="b")
        assert (await db.list_pronunciations())[0]["id"] == second["id"]
    finally:
        await db.close()


# ── schema evolution ─────────────────────────────────────────────────────────


async def test_added_columns_reach_a_database_that_predates_them(tmp_path: Path) -> None:
    """
    The case `CREATE TABLE IF NOT EXISTS` cannot handle, and the reason
    `_ADDED_COLUMNS` exists: a database created before `title` was added keeps
    its old shape forever, so every read of `row["title"]` would raise there.

    Builds a real old-shape database (schema.sql with the column stripped),
    inserts a row into it, then connects with the current code and asserts both
    that the column appeared and that the pre-existing row survived.
    """
    db_file = tmp_path / "old.db"
    old_schema = (
        (Path(__file__).parents[1] / "app" / "db" / "schema.sql")
        .read_text(encoding="utf-8")
        .replace("    title           TEXT,\n", "")
    )
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(old_schema)
        conn.execute(
            "INSERT INTO voice_profiles (name, audio_path, language) VALUES (?, ?, ?)",
            ("v", "/x/a.wav", "ur"),
        )
        conn.execute(
            """INSERT INTO generation_history
               (profile_id, input_text, language, output_path, model_id, transform,
                source_script, route_rationale)
               VALUES (1, 'old row', 'ur', '/x/o.wav', 'voxcpm2', 'none', 'arabic', 'r')""",
        )
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(generation_history)")}
        assert "title" not in cols, "fixture must actually predate the column"
    finally:
        conn.close()

    db = Database(db_file)
    await db.connect()
    try:
        row = await db.get_generation(1)
        assert row is not None
        assert row["input_text"] == "old row", "the existing row must survive"
        assert row["title"] is None, "the new column reads as NULL, not an error"

        # And the connect is idempotent — a second one must not fail on a
        # duplicate ADD COLUMN.
        await db.close()
        await db.connect()
        assert (await db.get_generation(1))["input_text"] == "old row"
    finally:
        await db.close()


async def test_title_round_trips_on_a_generation(tmp_path: Path) -> None:
    db = Database(tmp_path / "new.db")
    await db.connect()
    try:
        p = await db.create_profile(
            name="v", audio_path="/x/a.wav", language="ur", transcript=None,
            duration_sec=1.0, sample_rate=24000, peak_dbfs=-3.0, is_clipped=False,
        )
        g = await db.create_generation(
            profile_id=p["id"], input_text="میں دفتر جا رہا ہوں", language="ur",
            output_path="/x/o.wav", output_format="wav", duration_sec=2.0,
            gen_time_sec=1.0, model_id="omnivoice_urdu", transform="none",
            is_lossy=False, source_script="arabic", route_rationale="r",
            resolved_text=None, title="Office message",
        )
        assert g["title"] == "Office message"

        # Omitting it is still valid — nothing forces a title.
        g2 = await db.create_generation(
            profile_id=p["id"], input_text="x", language="ur",
            output_path="/x/o2.wav", output_format="wav", duration_sec=1.0,
            gen_time_sec=1.0, model_id="omnivoice_urdu", transform="none",
            is_lossy=False, source_script="arabic", route_rationale="r",
            resolved_text=None,
        )
        assert g2["title"] is None
    finally:
        await db.close()
