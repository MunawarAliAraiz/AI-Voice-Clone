"""Database access-layer tests against an in-memory SQLite (no files, no GPU)."""

from __future__ import annotations

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
