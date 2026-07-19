# AI Voice Clone Studio

Welcome to the AI Voice Clone Studio! This document outlines the core features of the application and provides comprehensive instructions on how to install, build, and use the software across different platforms.

---

## 🌟 Key Features

- **100% Offline & Private:** Your voice recordings and generated audio never leave your machine. Everything is processed locally.
- **Premium Glassmorphism UI:** A stunning, modern, dark-mode user interface with fluid micro-animations and a responsive design.
- **Real-Time Voice Recording:** Capture your voice directly within the app using your microphone, complete with a live interactive audio waveform.
- **Pluggable AI Architecture:** Designed to easily hot-swap heavy AI models (like F5-TTS, Fish Speech, or XTTS) based on your hardware capabilities.
- **Local Database:** Fast, serverless SQLite integration that permanently stores your voice profiles and generation history.
- **Cross-Platform Potential:** Built on Tauri (Rust) and React, the UI translates beautifully from desktop to mobile screens.

---

## 💻 How to Install and Use (PC Desktop App)

Because this app utilizes heavy AI models, it is designed primarily as a **Windows Desktop Application**.

### Option A: Development Mode (For Coding)
If you want to edit the code and test the app live:
1. Make sure you have **Node.js**, **Python 3.12**, and the **Microsoft C++ Build Tools** installed.
2. Clone this repository.
3. Open a terminal and build the Python sidecar:
   ```bash
   cd backend
   .\build-backend.ps1
   ```
4. Open a second terminal and launch the Tauri app:
   ```bash
   cd frontend
   npm install
   npm run tauri dev
   ```
   *The native app window will pop open immediately!*

### Option B: Building the Production Installer (`.exe` / `.msi`)
If you want to generate a professional, double-clickable installer file to share with others or install permanently:
1. Ensure the Python sidecar is built (Step 3 above).
2. Open your terminal in the `frontend` folder and run:
   ```bash
   npm run tauri build
   ```
3. Tauri will compile a highly-optimized release version of the app.
4. Once finished, navigate to `frontend/src-tauri/target/release/bundle/msi/`. You will find your final Installer file there!

---

## 📱 How to Use on Mobile (Android / iOS)

Because this application relies on a **Python sidecar** to run heavy GPU inference, it cannot be packaged directly into a native Android `.apk` or iOS `.ipa` file (phones cannot run local Python GPU servers).

However, the user interface was built to be **fully Mobile Responsive**. To use this on your phone, follow the **Cloud Architecture** approach:

1. **Host the AI Backend:** Deploy the `backend/` folder to a rented Cloud GPU server (like AWS, RunPod, or Lambda Labs) and expose it to the internet.
2. **Host the Frontend:** Deploy the `frontend/` React app to a free web host like **Vercel** or **Netlify**.
3. **Connect them:** Change the `API_BASE` URL in `frontend/src/services/api.ts` to point to your cloud server's IP address.
4. **Use on your phone:** Simply open the Vercel website link on your phone's browser. It will look and act exactly like a native mobile app, but the heavy AI lifting will happen instantly on the cloud server!
