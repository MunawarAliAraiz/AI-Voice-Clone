# AI Voice Clone Studio

A premium desktop application for personal voice cloning and multilingual text-to-speech generation.

## Features
- 🎙️ In-app voice recording with waveform visualization
- 🔊 Voice cloning using F5-TTS, Fish Speech, and XTTS v2
- 🌍 Multilingual support: English, Urdu, Hindi
- 📜 Generation history with playback
- ⚙️ Configurable settings and model management
- 🖥️ GPU-accelerated inference (NVIDIA CUDA)

## Tech Stack
- **Desktop**: Tauri v2 + React + TypeScript
- **Backend**: Python 3.11+ + FastAPI
- **AI Models**: F5-TTS, Fish Speech S2, XTTS v2 (from Hugging Face)
- **Database**: SQLite
- **Audio**: FFmpeg, torchaudio

## Development
```bash
# Frontend
cd frontend && npm install && npm run dev

# Backend
cd backend && uv sync && uv run python -m app.main

# Full app (after Tauri setup)
npm run tauri dev
```
