"""
Validation script for AI Voice Clone Studio engines & services
"""
import asyncio
from pathlib import Path
import wave
import struct

from app.database import init_database, get_db, close_db
from app.engines import EngineRegistry, get_available_engines, get_engine
from app.services.tts_service import generate_speech


async def main():
    print("=" * 60)
    print("[TEST] Running AI Model & Pipeline Verification Suite")
    print("=" * 60)

    # 1. Database Init
    await init_database()
    print("1. Database initialized successfully")

    # 2. Check registered engines
    engines = get_available_engines()
    engine_names = [e.name for e in engines]
    print(f"2. Registered engines ({len(engine_names)}): {engine_names}")
    assert "mock" in engine_names, "Mock engine must be registered"
    assert "f5_tts" in engine_names, "F5-TTS engine must be registered"
    assert "fish_speech" in engine_names, "Fish Speech engine must be registered"
    assert "xtts_v2" in engine_names, "XTTS v2 engine must be registered"

    # 3. Create dummy reference audio for testing
    dummy_wav = Path("test_ref.wav")
    with wave.open(str(dummy_wav), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        for i in range(22050):
            wf.writeframes(struct.pack("<h", 0))

    # 4. Insert dummy voice profile in DB
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO voice_profiles (id, name, audio_path, is_active)
               VALUES (999, 'Test Voice', 'test_ref.wav', 1)"""
        )
        await db.commit()
    finally:
        await close_db(db)

    print("3. Created test profile in DB with audio 'test_ref.wav'")

    # 5. Test Mock Engine Generation
    res = await generate_speech(
        text="Testing AI Voice Clone Studio generation pipeline.",
        profile_id=999,
        language="en",
        engine_name="mock",
        output_format="wav",
        emotion="happy",
        style="podcast",
    )
    print("4. Mock Generation Result:", res)
    assert Path(res["output_path"]).exists(), "Generated output file should exist"

    # Clean up test files
    if dummy_wav.exists():
        dummy_wav.unlink()

    print("=" * 60)
    print("[SUCCESS] ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
