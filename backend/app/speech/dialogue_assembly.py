"""Assemble completed local jobs without creating another inference job."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from .plans import file_sha256, wav_info


def assemble_dialogue(clips: list[tuple[int, Path, float]], output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dialogue-", dir=output.parent) as raw_work:
        work = Path(raw_work)
        normalized = []
        total_frames = 0
        for index, (job_id, path, pause) in enumerate(clips):
            digest = file_sha256(path)
            try:
                rate, channels, _ = wav_info(path)
            except (wave.Error, ValueError):
                rate, channels = 0, 0
            audio = path
            if (rate, channels) != (44100, 1):
                audio = work / f"{index}.wav"
                executable = shutil.which("ffmpeg")
                if executable is None:
                    raise ValueError("FFmpeg is required to normalize dialogue clips")
                result = subprocess.run(  # noqa: S603 - validated local paths, no shell
                    [
                        executable,
                        "-nostdin",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(path),
                        "-ar",
                        "44100",
                        "-ac",
                        "1",
                        "-c:a",
                        "pcm_s16le",
                        str(audio),
                    ],
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
                if result.returncode != 0:
                    raise ValueError("A dialogue clip could not be decoded")
            rate, channels, frames = wav_info(audio)
            pause_frames = round(pause * rate)
            total_frames += frames + pause_frames
            if total_frames > 3600 * rate:
                raise ValueError("Assembled dialogue may be at most one hour")
            normalized.append((job_id, audio, frames, pause_frames, digest))
        temporary = work / "assembled.wav"
        position, spans = 0, []
        with wave.open(str(temporary), "wb") as target:
            target.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            for job_id, audio, frames, pause_frames, digest in normalized:
                start = position
                with wave.open(str(audio), "rb") as source:
                    for chunk in iter(lambda: source.readframes(8192), b""):
                        target.writeframesraw(chunk)
                        position += len(chunk) // 2
                if position - start != frames:
                    raise ValueError("A dialogue clip was truncated during assembly")
                spans.append(
                    {
                        "job_id": job_id,
                        "start_sec": start / 44100,
                        "end_sec": position / 44100,
                        "source_sha256": digest,
                    }
                )
                remaining = pause_frames
                while remaining:
                    count = min(remaining, 8192)
                    target.writeframesraw(b"\x00\x00" * count)
                    position += count
                    remaining -= count
        os.replace(temporary, output)
        return {"duration_sec": position / 44100, "sha256": file_sha256(output), "spans": spans}
