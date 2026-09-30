param(
    [string]$Python = "python",
    [string]$Ffmpeg = "",
    [string]$FfmpegLicense = "",
    [string]$FfmpegBuildInfo = "",
    [switch]$UseExistingFrontendDependencies,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontend = Join-Path $repoRoot "frontend"
$backend = Join-Path $repoRoot "backend"
$dist = Join-Path $frontend "dist"
$tauriRoot = Join-Path $frontend "src-tauri"
$sidecarDir = Join-Path $tauriRoot "binaries"
$target = "x86_64-pc-windows-msvc"
$cliVersion = "2.12.0"
$buildRoot = Join-Path $repoRoot "build\desktop"
$pythonDist = Join-Path $buildRoot "dist"
$manifest = Join-Path $tauriRoot "Cargo.toml"
$missing = [System.Collections.Generic.List[string]]::new()

if ($env:OS -ne "Windows_NT") { $missing.Add("Build on 64-bit Windows.") }
foreach ($name in @("npm", "npx", "cargo", "rustc")) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        $missing.Add("$name is required. Install Rust MSVC and Node.js first.")
    }
}
if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    $missing.Add("Python executable was not found: $Python")
} else {
    & $Python -c 'import sys, struct; assert sys.version_info >= (3,12), "Python 3.12+ required"; assert struct.calcsize("P") == 8, "64-bit Python required"; import PyInstaller, fastapi, uvicorn, aiosqlite, aiofiles, httpx, soundfile, numpy, multipart, pydantic_settings; from mcp.server import MCPServer'
    if ($LASTEXITCODE -ne 0) {
        $missing.Add("Use a 64-bit Python 3.12+ environment with backend dependencies, PyInstaller, and MCP SDK v2 (backend/requirements-mcp.txt).")
    }
}
if (-not $CheckOnly) {
    foreach ($inputPath in @($Ffmpeg, $FfmpegLicense)) {
        if (-not $inputPath -or -not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
            $missing.Add("Supply -Ffmpeg and -FfmpegLicense paths for a redistributable static Windows FFmpeg build and its license notice.")
            break
        }
    }
}
if ($missing.Count) { throw ($missing -join [Environment]::NewLine) }
if ($CheckOnly) {
    Write-Output "Python imports, Node.js, and Rust commands are available. MSVC build tools and Windows SDK must also be installed."
    return
}

$ffmpegPath = (Resolve-Path -LiteralPath $Ffmpeg).Path
$licensePath = (Resolve-Path -LiteralPath $FfmpegLicense).Path
if (-not $FfmpegBuildInfo) {
    $FfmpegBuildInfo = Join-Path (Split-Path (Split-Path $ffmpegPath -Parent) -Parent) "README.txt"
}
if (-not (Test-Path -LiteralPath $FfmpegBuildInfo -PathType Leaf)) {
    throw "Supply -FfmpegBuildInfo with the publisher's README/configuration and source revision."
}
$buildInfoPath = (Resolve-Path -LiteralPath $FfmpegBuildInfo).Path
$ffmpegVersion = & $ffmpegPath -version 2>&1
if ($LASTEXITCODE -ne 0 -or -not ($ffmpegVersion -match '^ffmpeg version')) {
    throw "The supplied FFmpeg executable did not run."
}
if ($ffmpegVersion -match '--enable-shared') {
    throw "Supply a static FFmpeg build; DLL-based FFmpeg redistribution is not bundled by this script."
}
if ($ffmpegVersion -match '--enable-nonfree') {
    throw "The supplied FFmpeg build is marked nonfree and cannot be redistributed in this installer."
}

Push-Location $frontend
try {
    $nodeModules = Join-Path $frontend "node_modules"
    if ($UseExistingFrontendDependencies) {
        if (-not (Test-Path -LiteralPath $nodeModules -PathType Container)) {
            throw "Existing frontend dependencies were requested but node_modules is absent."
        }
    } else {
        if ((Test-Path -LiteralPath $nodeModules) -and
            ((Get-Item -LiteralPath $nodeModules).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "node_modules is a junction or symlink. Remove the link or explicitly use -UseExistingFrontendDependencies before building."
        }
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
} finally { Pop-Location }

New-Item -ItemType Directory -Force -Path $sidecarDir, $buildRoot | Out-Null
$commonArgs = @("--noconfirm", "--clean", "--onefile", "--noupx", "--paths", $backend,
    "--distpath", $pythonDist, "--workpath", (Join-Path $buildRoot "pyinstaller"),
    "--specpath", $buildRoot, "--exclude-module", "torch", "--exclude-module", "torchaudio")
Push-Location $repoRoot
try {
    & $Python -m PyInstaller @commonArgs --name voice-clone-api --console `
        --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto `
        --hidden-import uvicorn.lifespan.on --collect-all _soundfile_data `
        --add-data "$(Join-Path $backend 'app\db\schema.sql');app/db" `
        --add-data "${dist};web" (Join-Path $backend "desktop_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "Python API sidecar build failed" }
    # Stdio must remain attached for Codex/Claude MCP clients.
    & $Python -m PyInstaller @commonArgs --name voice-clone-mcp --console `
        --collect-submodules mcp.server --copy-metadata mcp (Join-Path $backend "mcp_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "Python MCP executable build failed" }
    foreach ($binary in @("voice-clone-api", "voice-clone-mcp")) {
        Copy-Item -LiteralPath (Join-Path $pythonDist "$binary.exe") `
            -Destination (Join-Path $sidecarDir "$binary-$target.exe") -Force
    }
    Copy-Item -LiteralPath $ffmpegPath -Destination (Join-Path $sidecarDir "ffmpeg-$target.exe") -Force
    Copy-Item -LiteralPath $licensePath -Destination (Join-Path $sidecarDir "ffmpeg-license.txt") -Force
    Copy-Item -LiteralPath $buildInfoPath -Destination (Join-Path $sidecarDir "ffmpeg-build-info.txt") -Force
} finally { Pop-Location }

if (-not (Test-Path -LiteralPath (Join-Path $tauriRoot "Cargo.lock"))) {
    & cargo generate-lockfile --manifest-path $manifest
    if ($LASTEXITCODE -ne 0) { throw "Cargo lockfile generation failed" }
    Write-Output "Generated Cargo.lock. Preserve it with the release source for repeatable builds."
}
Push-Location $frontend
try {
    npx --yes "@tauri-apps/cli@$cliVersion" build --target $target --bundles nsis -- --locked
    if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS build failed" }
} finally { Pop-Location }

$installerDir = Join-Path $tauriRoot "target\$target\release\bundle\nsis"
$installers = @(Get-ChildItem -LiteralPath $installerDir -Filter "*.exe")
if (-not $installers.Count) { throw "The build did not produce an NSIS installer." }
$packages = & $Python -c 'import importlib.metadata as m; print(chr(10).join(sorted(d.metadata.get("Name", "")+"=="+d.version for d in m.distributions())))'
if ($LASTEXITCODE -ne 0) { throw "Could not record Python build dependencies." }
$receipt = [ordered]@{
    tauriCli = $cliVersion
    target = $target
    frontendLockSha256 = (Get-FileHash -LiteralPath (Join-Path $frontend "package-lock.json") -Algorithm SHA256).Hash
    pythonPackages = @($packages)
    cargoLockSha256 = (Get-FileHash -LiteralPath (Join-Path $tauriRoot "Cargo.lock") -Algorithm SHA256).Hash
    ffmpegSha256 = (Get-FileHash -LiteralPath $ffmpegPath -Algorithm SHA256).Hash
    artifacts = @($installers | ForEach-Object {
        @{ file = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $installerDir "build-receipt.json") -Encoding UTF8
$installers | Select-Object -ExpandProperty FullName
