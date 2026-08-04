# AI Voice Clone Studio — Upgrades & Roadmap

> **⚠️ Partially superseded by [docs/REWRITE_PLAN.md](docs/REWRITE_PLAN.md).** Kept as a record of
> intent, but check the plan before acting on anything here — several items were reconsidered:
>
> - **Emotion & tone sliders** were *removed*, not added. The nine presets that existed resolved to
>   an `atempo` multiplier, with text preprocessing that was a no-op on Urdu and Hindi — the target
>   languages. They are replaced by real per-model parameters (e.g. Chatterbox's `exaggeration`),
>   with the UI hiding controls the selected model does not declare.
> - **Studio-grade preprocessing** landed as the audio pipeline, plus a level meter and clipping
>   indicator at record time — reference quality dominates clone quality more than any model knob.
> - The **desktop shell was dropped**; this is a web app now.
> - XTTS v2 is gone (non-commercial license), so items referencing its prompt tokens no longer apply.

While the foundation of the AI Voice Clone Studio is highly robust, transitioning it from a strong prototype into a **world-class, commercial-grade professional application** requires several key feature additions and architectural improvements. 

Here is the detailed roadmap for future development:

---

## 1. Advanced AI & Audio Capabilities

### ⚡ Real-Time Audio Streaming (Chunking)
- **Current State:** The user clicks "Generate", the Python backend processes the entire sentence, and returns a single `.wav` file after it finishes.
- **Upgrade:** Implement WebSockets or Server-Sent Events (SSE) to stream audio in chunks. As soon as the first word is synthesized by the AI, it starts playing in the UI while the rest of the sentence is still generating.

### 🎛️ Emotion & Tone Control
- **Current State:** The AI mimics the voice accurately but relies on punctuation for tone.
- **Upgrade:** Add sliders in the UI to control **Emotion** (Happy, Sad, Angry, Whispering) and **Pacing** (Speed, Pitch). This would require hooking into specific prompt-tokens depending on the AI model (like XTTS v2).

### 🔇 Studio-Grade Audio Preprocessing
- **Current State:** The user speaks into their laptop microphone, and that raw audio is used as the reference.
- **Upgrade:** Integrate `ffmpeg` filters and a library like `rnnoise` to automatically strip out background static, hums, and room echo before passing the voice to the AI. Better input equals significantly better output.

### 🧠 Model Fine-Tuning (LoRA)
- **Current State:** The app uses "Zero-Shot" cloning (3 seconds of audio = instant clone).
- **Upgrade:** Add a "Pro Training" feature where users can read a 2-minute script. The backend will use `LoRA` (Low-Rank Adaptation) to actually train a mini-neural network on their voice, resulting in a 99% perfect, artifact-free clone.

---

## 2. Professional UI / UX Polish

### ✂️ In-App Audio Trimming
- **Upgrade:** When a user records their voice, add a visual waveform editor allowing them to drag sliders to trim out the "silence" at the beginning and end of the recording before saving the profile.

### 📤 Advanced Exporting & Sharing
- **Upgrade:** Add the ability to export the generated audio in multiple formats (`.mp3`, `.ogg`, `.flac`) directly from the History page, rather than just `.wav`.
- **Upgrade:** Add a "Share" button to instantly copy a cloud link (if hosted) or share directly to social media.

### 🖱️ Drag-and-Drop System
- **Upgrade:** Allow users to drag a `.wav` or `.mp3` file from their desktop directly onto the app window to instantly create a new Voice Profile, bypassing the file-picker dialog entirely.

---

## 3. Architecture & Enterprise Readiness

### 🔄 Auto-Updater
- **Upgrade:** Utilize Tauri's built-in `updater` module. When you push a new version to GitHub, the app will automatically notify users "A new update is available!" and download/install the patch silently in the background, exactly like Discord or Spotify.

### 🐳 Docker & 1-Click Cloud Deployment
- **Upgrade:** Create a `docker-compose.yml` file. This allows users to deploy the entire backend to a rented cloud GPU server (like RunPod or AWS) using a single command, making the transition from "Local Desktop App" to "Cloud Hosted Web App" absolutely seamless.

### 🔒 User Authentication & Security (If Web Hosted)
- **Upgrade:** If you choose to host this on the web for thousands of users, the SQLite database needs to be migrated to **PostgreSQL**. You must add JWT (JSON Web Token) authentication so users must log in, and their voice profiles are strictly isolated and encrypted.

---

## Summary of Next Immediate Steps
When you boot up your new **RTX 4060 PC**, the very first things you should implement from this list are:
1. Downloading the real `F5-TTS` model weights.
2. Writing the `ffmpeg` pre-processor to clean the microphone audio.
3. Implementing the Tauri Auto-Updater.
