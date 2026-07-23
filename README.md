# AI Voice Clone Studio

A premium desktop application for personal voice cloning and multilingual text-to-speech generation.

## Features
- 🎙️ **Voice Profile Management:** Record via browser MediaRecorder with live waveform visualization or upload audio files (.webm, .wav, .mp3, .ogg, .flac).
- 🔊 **Multi-Engine Zero-Shot Voice Cloning:** Hot-swappable AI engines including F5-TTS, Fish Speech S2, XTTS v2, and Mock Engine.
- ⚡ **Dynamic Engine Registry & VRAM Manager:** `@register_engine` decorator for Open/Closed model extensibility with automated GPU model offloading & CUDA cache clearing.
- 🌐 **Neural Translation Service:** Independent Meta NLLB-200 translation (Urdu ↔ Hindi ↔ English) with zero-latency script language detection and 2-tier memory + SQLite database caching.
- 🎛️ **Modular Audio Processing Pipeline:** EBU R128 loudness normalization, leading/trailing silence trimming, spectral noise reduction, and reference format conversion via single-pass FFmpeg filter graphs.
- 📜 **Generation History & Playback:** Local persistence of audio clips, favorites tagging, and MP3/WAV downloads.
- 🖥️ **GPU Acceleration & CPU Fallback:** Automatic CUDA GPU detection with seamless CPU fallback recovery.

## Tech Stack
- **Desktop Shell / Frontend:** Tauri v2 + React 18 + TypeScript + Vite
- **Backend Service:** Python 3.10+ + FastAPI (Uvicorn ASGI)
- **AI Models:** Meta NLLB-200, F5-TTS, Fish Speech S2, XTTS v2
- **Audio Processing Engine:** C-Native FFmpeg CLI, PyTorch, torchaudio
- **Database:** SQLite via `aiosqlite` (WAL Mode)

## Quick Start (Development Mode)
```bash
# Install frontend dependencies
cd frontend && npm install

# Install backend dependencies
cd ../backend && pip install -r requirements.txt

# Run concurrent dev server (Frontend SPA + Python Backend)
cd ../frontend && npm run dev
```

