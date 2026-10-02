param(
    [string]$Python = "python",
    [string]$TauriCli = "",
    [string]$UpdaterKeyFile = "",
    [ValidateRange(1, 64)][int]$BuildJobs = 2,
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
if ($missing.Count) { throw ($missing -join [Environment]::NewLine) }
if ($TauriCli) {
    if (-not (Test-Path -LiteralPath $TauriCli -PathType Leaf)) {
        throw "Cached Tauri CLI JavaScript entry point was not found: $TauriCli"
    }
    $TauriCli = (Resolve-Path -LiteralPath $TauriCli).Path
    $cachedCliVersion = & node $TauriCli --version
    if ($LASTEXITCODE -ne 0 -or $cachedCliVersion -notmatch "^tauri-cli $([regex]::Escape($cliVersion))$") {
        throw "The supplied cached CLI must be Tauri $cliVersion."
    }
}
if ($CheckOnly) {
    Write-Output "Python imports, Node.js, and Rust commands are available. MSVC build tools and Windows SDK must also be installed."
    return
}

$cloudRelease = Join-Path $backend "app\runpod\release.json"
if (-not (Test-Path -LiteralPath $cloudRelease -PathType Leaf)) {
    throw "Qualify both published worker images with scripts/prepare-pod-release.py before building the desktop installer."
}
$cloudEvidence = Get-Content -LiteralPath $cloudRelease -Raw | ConvertFrom-Json
foreach ($role in @("installer", "gpu")) {
    $reference = $cloudEvidence.$role
    if ($reference -notmatch "^ghcr\.io/munawaraliaraiz/ai-voice-clone-$role@sha256:[a-f0-9]{64}$" -or
        $cloudEvidence.evidence.$role.registry.anonymous_pull -ne "passed") {
        throw "Cloud release evidence is missing a qualified immutable $role image."
    }
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
    $cloudDataArgs = @("--add-data", "${cloudRelease};app/runpod")
    & $Python -m PyInstaller @commonArgs --name voice-clone-api --console `
        --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto `
        --hidden-import uvicorn.lifespan.on --collect-all _soundfile_data `
        --add-data "$(Join-Path $backend 'app\db\schema.sql');app/db" `
        --add-data "${dist};web" @cloudDataArgs (Join-Path $backend "desktop_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "Python API sidecar build failed" }
    # Stdio must remain attached for Codex/Claude MCP clients.
    & $Python -m PyInstaller @commonArgs --name voice-clone-mcp --console `
        --collect-submodules mcp.server --copy-metadata mcp (Join-Path $backend "mcp_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "Python MCP executable build failed" }
    foreach ($binary in @("voice-clone-api", "voice-clone-mcp")) {
        Copy-Item -LiteralPath (Join-Path $pythonDist "$binary.exe") `
            -Destination (Join-Path $sidecarDir "$binary-$target.exe") -Force
    }
} finally { Pop-Location }

if (-not (Test-Path -LiteralPath (Join-Path $tauriRoot "Cargo.lock"))) {
    & cargo generate-lockfile --manifest-path $manifest
    if ($LASTEXITCODE -ne 0) { throw "Cargo lockfile generation failed" }
    Write-Output "Generated Cargo.lock. Preserve it with the release source for repeatable builds."
}
Push-Location $frontend
try {
    $ownedSigningKey = $false
    if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
        if (-not $UpdaterKeyFile -or -not (Test-Path -LiteralPath $UpdaterKeyFile -PathType Leaf)) {
            throw "Supply -UpdaterKeyFile for the Windows DPAPI protected signing identity, or set TAURI_SIGNING_PRIVATE_KEY securely."
        }
        $protectedKey = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $UpdaterKeyFile).Path)
        $keyBytes = [Security.Cryptography.ProtectedData]::Unprotect($protectedKey,
            [Text.Encoding]::UTF8.GetBytes('VoiceCloneUpdaterSigningV1'),
            [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $env:TAURI_SIGNING_PRIVATE_KEY = [Text.Encoding]::UTF8.GetString($keyBytes)
        [Array]::Clear($keyBytes, 0, $keyBytes.Length)
        $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ''
        $ownedSigningKey = $true
    }
    if ($TauriCli) {
        & node $TauriCli build --target $target --bundles nsis -- --locked --jobs $BuildJobs
    } else {
        npx --yes "@tauri-apps/cli@$cliVersion" build --target $target --bundles nsis -- --locked --jobs $BuildJobs
    }
    if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS build failed" }
} finally {
    if ($ownedSigningKey) {
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
    }
    Pop-Location
}

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
    cloudReleaseSha256 = (Get-FileHash -LiteralPath $cloudRelease -Algorithm SHA256).Hash
    audioToolsDelivery = "First-run publisher download; no FFmpeg binary bundled"
    artifacts = @($installers | ForEach-Object {
        @{ file = $_.Name; bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $installerDir "build-receipt.json") -Encoding UTF8
$installers | Select-Object -ExpandProperty FullName
