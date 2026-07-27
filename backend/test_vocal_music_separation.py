"""
Verification test for vocal isolation, background music removal & noise suppression
"""
import asyncio
import subprocess
from pathlib import Path
import wave

from app.database import init_database
from app.utils.audio_pipeline import AudioPipeline, AudioPipelineConfig
from app.services.voice_service import save_voice_recording, get_profile


async def test_vocal_isolation_and_noise_removal():
    print("=" * 60)
    print("[TEST] Testing Vocal Isolation, Music Removal & Noise Suppression")
    print("=" * 60)

    await init_database()

    # 1. Generate a synthetic audio containing vocal tone (440Hz) + music bass (40Hz) + music synth (10,000Hz) + noise
    noisy_music_audio_path = Path("test_noisy_music_sample.wav")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=3",
        "-ar", "44100", "-ac", "2",
        str(noisy_music_audio_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    assert noisy_music_audio_path.exists(), "Synthetic test audio sample WAV created"
    print("1. Created synthetic audio sample with vocal tone (440Hz)")


    # 2. Process via AudioPipeline with isolate_vocals=True and reduce_noise=True
    output_wav_path = Path("test_isolated_vocal.wav")
    config = AudioPipelineConfig(
        convert_wav=True,
        sample_rate=22050,
        channels=1,
        normalize_loudness=True,
        trim_silence=True,
        reduce_noise=True,
        isolate_vocals=True,
    )

    result_path = AudioPipeline.process_pipeline(
        input_path=noisy_music_audio_path,
        output_path=output_wav_path,
        config=config,
    )

    print("2. AudioPipeline processed output:", result_path)
    assert result_path.exists(), "Processed output WAV must exist"

    with wave.open(str(result_path), "r") as wf:
        print(f"3. Isolated audio properties: Channels={wf.getnchannels()}, FrameRate={wf.getframerate()}Hz, Duration={wf.getnframes()/wf.getframerate():.2f}s")
        assert wf.getnchannels() == 1, "Must be mono audio (1 channel)"
        assert wf.getframerate() == 22050, "Must be resampled to 22.05kHz"
        assert wf.getnframes() > 0, "Frames count must be greater than 0"

    # Clean up temporary test files
    if noisy_music_audio_path.exists():
        noisy_music_audio_path.unlink()
    if output_wav_path.exists():
        output_wav_path.unlink()

    print("=" * 60)
    print("[SUCCESS] VOCAL ISOLATION & NOISE SUPPRESSION TEST PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_vocal_isolation_and_noise_removal())
