"""Database access-layer tests against an in-memory SQLite (no files, no GPU)."""

from __future__ import annotations

import sqlite3

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
