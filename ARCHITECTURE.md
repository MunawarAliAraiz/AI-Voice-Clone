# AI Voice Clone Studio — Architecture & Program Flow

This document details the internal workings of the AI Voice Clone Studio, the purpose of each major component, and a detailed checklist of what is currently incomplete and needs to be addressed when migrating to the new RTX GPU workstation.

---

## 1. High-Level Program Flow

The application is built using a **Sidecar Architecture**. It consists of a native desktop shell (Tauri) running a React frontend, which communicates seamlessly with a local Python backend running FastAPI.

### Startup Flow
1. The user opens the `.exe`.
2. **Tauri (Rust)** launches the native application window.
3. Before rendering the UI, Tauri's `tauri-plugin-shell` spawns a hidden background process: the **Python Backend** (`backend-x86_64-pc-windows-msvc.exe`).
4. The Python backend spins up a local web server (FastAPI) on `http://localhost:8000` and initializes the SQLite database.
5. The React frontend loads, pings the backend `GET /health` endpoint, and turns the Sidebar indicator "Green" (Connected).

### Voice Recording Flow
1. User navigates to the **Record** page and clicks the microphone.
2. The browser's `MediaRecorder` API captures the audio, while the `Canvas` API draws a real-time waveform.
3. When stopped, the audio is sent as a `FormData` POST request to `POST /api/voice/upload`.
4. FastAPI saves the `.webm` audio to disk (`data/voices/`), creates a database record in SQLite, and returns the profile ID.

### Speech Generation Flow
1. User navigates to the **Generate** page, selects a voice profile, enters text, and clicks Generate.
2. The frontend sends a `POST /api/tts/generate` request containing the text, target language, and voice profile ID.
3. The backend retrieves the voice profile path from SQLite.
4. The backend routes the request to the **Active TTS Engine** (currently the `MockTTSEngine`).
5. The engine synthesizes the speech and saves it as a `.wav` file in `data/history/`.
6. FastAPI logs the generation in the history table and returns the audio URL to the frontend, which plays it via the `<audio>` player.

---

## 2. Purpose of Everything (Folder Structure)

### `frontend/` (The User Interface)
Built with React, TypeScript, and Vite. Designed to feel like a premium, native application using glassmorphism and modern CSS.
- **`src-tauri/`**: Contains the Rust configuration. This bridges the gap between a web app and a Windows desktop app. `tauri.conf.json` defines the window size and the Python "sidecar" executable.
- **`src/pages/`**:
  - `DashboardPage`: Overview of system stats and recent activity.
  - `RecordPage`: Captures user voice profiles using the microphone.
  - `GeneratePage`: The main workspace where text is converted to speech.
  - `HistoryPage`: A gallery of past generated audio files.
  - `SettingsPage`: Allows switching between AI engines and viewing hardware status.
- **`src/services/api.ts`**: The central networking hub. All communication with the Python backend goes through this file.
- **`src/styles/`**: Global CSS variables, custom animations, and layout grids.

### `backend/` (The Brain)
Built with Python and FastAPI. Responsible for heavy lifting, database management, and AI inference.
- **`app/main.py`**: The entry point. Configures CORS, loads routers, and manages application startup/shutdown.
- **`app/database.py`**: Handles the asynchronous SQLite connection (`aiosqlite`). Stores voice metadata and generation history.
- **`app/routers/`**:
  - `voice.py`: Endpoints for uploading and managing voice profiles.
  - `tts.py`: Endpoints for triggering text-to-speech generation.
  - `history.py`: Endpoints for fetching and deleting past audio clips.
  - `settings.py`: Endpoints for managing the active TTS engine.
- **`app/engines/`**: The pluggable AI architecture.
  - `base.py`: Defines the `TTSEngine` interface. Every AI model must follow this blueprint.
  - `mock_engine.py`: Instantly generates a 1-second beep. **Used currently to allow UI development without a GPU.**
  - `f5_tts.py / fish_speech.py`: The wrapper classes where the actual Hugging Face model inference code will live.
- **`build-backend.ps1`**: A PowerShell script that uses `PyInstaller` to bundle the entire Python folder into a single `.exe` file that Tauri can launch.

---

## 3. What is Incomplete? (The Migration Checklist)

The application currently has a completely finished UI, database, and API layer. However, because it was developed on an Intel Iris Xe laptop, the heavy AI logic was purposely stubbed out. 

**When migrating to the new RTX 4060 PC, the following must be completed:**

### 1. Integrate Real AI Models (Python Backend)
- **Status:** *Incomplete.* Currently using `MockTTSEngine`.
- **Action Required:** 
  1. Install GPU-enabled PyTorch: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121`
  2. Install model libraries: `pip install f5-tts transformers`
  3. Write the actual inference code inside `backend/app/engines/f5_tts.py`. The code must load the model into VRAM (`.to("cuda")`), process the reference audio, and generate the output `.wav`.

### 2. Audio Pre-Processing (FFmpeg)
- **Status:** *Incomplete.*
- **Action Required:** AI models require pristine `.wav` files at specific sample rates (e.g., 24kHz). The web frontend records in `.webm` format. We must install `ffmpeg-python` in the backend and write a script to normalize and convert the `.webm` uploads into `.wav` before passing them to the AI engine.

### 3. Model Weight Downloader
- **Status:** *Incomplete.*
- **Action Required:** AI models require gigabytes of weight files (`.bin` or `.pt`). We need a utility in the backend that automatically downloads these files from Hugging Face into a `backend/models/` folder if they are missing on startup.

### 4. Native Windows `.exe` Compilation
- **Status:** *Incomplete.*
- **Action Required:** The user must install the **Visual Studio 2022 C++ Build Tools** on the target machine. Once installed, running `npm run tauri build` will successfully generate the final, double-clickable installer file.

### 5. Error Handling & Edge Cases
- **Status:** *Partially Complete.*
- **Action Required:** We need to add `try/except` blocks in the AI inference code to catch "CUDA Out of Memory" errors and gracefully send a message to the React frontend, rather than crashing the backend sidecar.
