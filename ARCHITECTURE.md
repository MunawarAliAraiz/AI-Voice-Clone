# AI Voice Clone Studio — Architecture & Program Flow

This document details the internal workings of the AI Voice Clone Studio, the technology stack, the API routes, and how to run the application in various environments.

---

## 1. Technology Stack

### Frontend Stack
- **Framework:** React 18 (with TypeScript)
- **Build Tool:** Vite (for Lightning-fast HMR and bundling)
- **Desktop Shell:** Tauri v2 (Rust-based, extremely lightweight alternative to Electron)
- **Styling:** Vanilla CSS with CSS Variables (No Tailwind/Bootstrap) for complete custom control, glassmorphism, and responsive design.
- **Web APIs used:** 
  - `MediaRecorder API` (To record microphone audio directly in-app)
  - `Canvas API` (To draw the live audio waveform)

### Backend Stack
- **Framework:** FastAPI (Python) for asynchronous, high-performance REST APIs.
- **Server:** Uvicorn (ASGI web server implementation for Python).
- **Database:** `aiosqlite` (Asynchronous SQLite for storing profiles and history locally without needing a heavy database server).
- **Packaging:** PyInstaller (To compile the Python code into a standalone `.exe` sidecar).

### AI & Machine Learning Stack (Pending GPU)
- **Core:** PyTorch (`torch`, `torchaudio`)
- **Models:** F5-TTS / Fish Speech / XTTS v2 (Local open-source Hugging Face models)
- **Audio Processing:** FFmpeg (via `ffmpeg-python`) to normalize user recordings.

---

## 2. Internal API Specifications

The application is completely **offline** and privacy-first. It does not use external cloud APIs (like OpenAI or ElevenLabs). Instead, the React frontend talks strictly to the local Python backend using the following REST APIs:

### System
- `GET /health` — Checks if the backend is online and the database is accessible.

### Voice Profiles
- `POST /api/voice/upload` — Accepts a `multipart/form-data` `.webm` audio file, saves it to disk, and creates a database record.
- `GET /api/voice/profiles` — Retrieves a list of all saved voice profiles.
- `DELETE /api/voice/profiles/{id}` — Soft-deletes a voice profile from the database.

### Speech Generation (TTS)
- `POST /api/tts/generate` — The core endpoint. Accepts `text`, `voice_id`, and `language`. Routes the request to the active AI engine, generates the `.wav` file, and saves it to history.

### History & Settings
- `GET /api/history` — Retrieves the history of all previously generated audio clips.
- `GET /api/settings/engine` — Retrieves the currently active TTS engine (e.g., `mock`, `f5-tts`).

---

## 3. High-Level Program Flow

### Startup Flow
1. **Tauri (Rust)** launches the native application window.
2. Tauri's `tauri-plugin-shell` spawns a hidden background process: the **Python Backend** (`backend-x86_64-pc-windows-msvc.exe`).
3. The Python backend spins up FastAPI on `http://localhost:8000` and initializes SQLite.
4. The React frontend loads, pings `GET /health`, and turns the Sidebar indicator "Green" (Connected).

### Voice Recording Flow
1. User clicks the microphone. The browser's `MediaRecorder` captures audio, while `Canvas` draws a waveform.
2. Audio is sent to `POST /api/voice/upload`.
3. FastAPI saves the file to `data/voices/` and returns the profile ID.

### Speech Generation Flow
1. User enters text and clicks Generate.
2. Frontend calls `POST /api/tts/generate`.
3. Backend retrieves the voice profile path, routes it to the **Active TTS Engine**, synthesizes the speech, and saves it to `data/history/`.

---

## 4. How to Run the Application

### Option A: Running Locally (Development Mode)
To run the application on your own laptop or PC while editing the code:
1. Open a terminal inside the `frontend` folder.
2. Run `npm run tauri dev` (Requires C++ Build tools).
3. **Alternative shortcut:** Run `npm run dev`. This uses `concurrently` to launch the Vite web browser version and the Python backend simultaneously, bypassing the need for native Rust compilation.

### Option B: Running Locally (Production `.exe`)
Once you install the Microsoft Visual Studio 2022 C++ Build Tools:
1. Build the backend: `cd backend && .\build-backend.ps1`
2. Build the frontend: `cd frontend && npm run tauri build`
3. Double click the resulting `.exe` installer. It will install on your system and run entirely offline with a double-click!

### Option C: Running on a Rented Cloud Server
If you rent a powerful GPU server (like RunPod, AWS, or Lambda Labs) and want to run the heavy AI there, but use the UI on your laptop, you must pivot the architecture slightly:

1. **Host the Backend on the Cloud:**
   - Clone the repository onto the rented server.
   - Run the Python backend only: `python -m app.main`
   - Use a tool like `ngrok`, `Cloudflare Tunnels`, or open port `8000` to expose the backend API to the public internet (e.g., `https://my-gpu-server.ngrok.app`).
2. **Modify the Frontend on your Laptop:**
   - On your local laptop, open `frontend/src/services/api.ts`.
   - Change `const API_BASE_URL = "http://localhost:8000"` to point to your cloud server URL (`https://my-gpu-server.ngrok.app`).
   - Run the frontend locally via `npm run dev`.
   
*Result:* Your local laptop will render the beautiful UI and record your voice, but the moment you click "Generate", the heavy lifting will be sent to the rented cloud GPU, and the audio will be streamed back to your laptop!

---

## 5. What is Incomplete? (The Migration Checklist)
When migrating to the new RTX 4060 PC, the following must be completed:
1. **Integrate Real AI Models:** Install `torch` (CUDA) and `f5-tts`. Write the inference code in `app/engines/f5_tts.py`.
2. **Audio Pre-Processing:** Install `ffmpeg-python` to normalize the `.webm` microphone uploads into clean `.wav` files before AI processing.
3. **Model Weights Downloader:** Write a utility to download the gigabytes of `.bin` weights from Hugging Face on startup.
4. **Compile the App:** Install MSVC Build Tools and run `npm run tauri build`.
