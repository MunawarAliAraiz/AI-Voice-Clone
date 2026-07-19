# AI Voice Clone Studio — Backend Build Script
# This script bundles the Python FastAPI backend into a single executable
# and moves it to the Tauri src-tauri/bin/ directory as a sidecar.

$ErrorActionPreference = "Stop"

Write-Host "⚙️ Building Python Backend with PyInstaller..." -ForegroundColor Cyan

# 1. Compile with PyInstaller
pyinstaller --noconfirm --onefile --windowed --name backend run.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ PyInstaller failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "✅ Backend compilation successful." -ForegroundColor Green

# 2. Ensure Tauri bin directory exists
$TauriBinDir = "..\frontend\src-tauri\bin"
if (-Not (Test-Path $TauriBinDir)) {
    New-Item -ItemType Directory -Force -Path $TauriBinDir | Out-Null
}

# 3. Move and rename the executable to match Tauri's expected sidecar format
# Tauri expects the target triple appended to the sidecar name.
# Windows 64-bit target triple is typically x86_64-pc-windows-msvc
$SourceExe = "dist\backend.exe"
$DestExe = "$TauriBinDir\backend-x86_64-pc-windows-msvc.exe"

Move-Item -Path $SourceExe -Destination $DestExe -Force

Write-Host "✅ Moved executable to: $DestExe" -ForegroundColor Green
Write-Host "🎉 Backend sidecar is ready for Tauri build!" -ForegroundColor Magenta
