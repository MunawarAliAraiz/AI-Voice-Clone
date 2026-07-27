"""
Verification test for video upload & automatic audio extraction
"""
import asyncio
import subprocess
from pathlib import Path
import wave

from app.database import init_database
from app.services.voice_service import save_voice_recording, get_profile


async def test_video_audio_extraction():
    print("=" * 60)
    print("[TEST] Testing Video Upload & Automatic Audio Extraction")
    print("=" * 60)

    # Initialize DB
    await init_database()

    # 1. Generate a synthetic test MP4 video with audio stream via FFmpeg
    test_video_path = Path("test_sample_video.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-c:a", "aac",
        str(test_video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    assert test_video_path.exists(), "Synthetic test video should be created"
    print("1. Synthetic test video (MP4) created successfully")

    # 2. Read video file bytes and save via save_voice_recording
    video_bytes = test_video_path.read_bytes()
    profile = await save_voice_recording(
        file_content=video_bytes,
        filename="test_sample_video.mp4",
        name="Test Video Profile",
        transcript="Test video audio extraction transcript",
        language="en",
    )

    print("2. Voice profile created from video upload result:", profile)

    # 3. Assert profile created and audio extracted as WAV
    profile_id = profile["id"]
    db_profile = await get_profile(profile_id)
    extracted_wav_path = Path(db_profile["audio_path"])

    assert extracted_wav_path.exists(), "Extracted reference WAV file must exist on disk"
    assert extracted_wav_path.suffix == ".wav", "Extracted reference file must have .wav extension"
    assert db_profile["duration_sec"] is not None and db_profile["duration_sec"] > 0, "Duration must be greater than 0"

    # Verify extracted WAV header details (mono 22.05kHz)
    with wave.open(str(extracted_wav_path), "r") as wf:
        n_channels = wf.getnchannels()
        frame_rate = wf.getframerate()
        print(f"3. Extracted WAV properties: Channels={n_channels}, FrameRate={frame_rate}Hz, Duration={wf.getnframes()/frame_rate:.2f}s")
        assert n_channels == 1, "Extracted audio should be mono (1 channel)"
        assert frame_rate == 22050, "Extracted audio should be resampled to 22.05kHz"

    # Clean up test video
    if test_video_path.exists():
        test_video_path.unlink()

    print("=" * 60)
    print("[SUCCESS] VIDEO AUDIO EXTRACTION TEST PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_video_audio_extraction())
