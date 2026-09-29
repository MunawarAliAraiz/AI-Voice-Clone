param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontend = Join-Path $repoRoot "frontend"
$backend = Join-Path $repoRoot "backend"
$dist = Join-Path $frontend "dist"
$sidecarDir = Join-Path $frontend "src-tauri\binaries"
$sidecarName = "voice-clone-api-x86_64-pc-windows-msvc.exe"

foreach ($name in @("npm", "cargo")) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name is required to build the Windows installer."
    }
}

Push-Location $frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
} finally { Pop-Location }

& $Python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Install PyInstaller in the chosen Python environment." }

Push-Location $repoRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --noupx `
        --name voice-clone-api --paths $backend `
        --add-data "$(Join-Path $backend 'app\db\schema.sql');app/db" `
        --add-data "${dist};web" `
        (Join-Path $backend "desktop_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "Python sidecar build failed" }
    New-Item -ItemType Directory -Force -Path $sidecarDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot "dist\voice-clone-api.exe") `
        -Destination (Join-Path $sidecarDir $sidecarName) -Force
} finally { Pop-Location }

Push-Location $frontend
try {
    npx --yes @tauri-apps/cli@2 build --bundles nsis
    if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS build failed" }
} finally { Pop-Location }

Get-ChildItem (Join-Path $frontend "src-tauri\target\release\bundle\nsis") -Filter "*.exe" |
    Select-Object -ExpandProperty FullName
