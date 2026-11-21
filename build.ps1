# build.ps1 - build IPTV-Overlay-Server for Windows
# NOTE: ASCII-only to avoid encoding issues in Windows PowerShell 5.1

$ErrorActionPreference = "Stop"

Write-Host "=== Building IPTV-Overlay-Server ===" -ForegroundColor Cyan

# 1. Clean previous build
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }

# 2. PyInstaller (onedir)
Write-Host "[1/3] Running PyInstaller..." -ForegroundColor Yellow

$pyArgs = @(
    "--noconfirm",
    "--windowed",
    "--name", "IPTV-Overlay-Server",
    "--icon", "assets/icon.ico",
    "--hidden-import", "uvicorn",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.loops.asyncio",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.http.h11_impl",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "fastapi",
    "--hidden-import", "starlette",
    "main.py"
)

$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -m PyInstaller @pyArgs

$dist = "dist\IPTV-Overlay-Server"

# 3. Copy user-facing files next to .exe
Write-Host "[2/3] Copying README, LICENSE, assets..." -ForegroundColor Yellow
Copy-Item "README.md" $dist
Copy-Item "LICENSE"   $dist
Copy-Item -Recurse "assets" $dist

# 4. Copy FFmpeg
Write-Host "[3/3] Copying FFmpeg..." -ForegroundColor Yellow
if (Test-Path "ffmpeg_bin\ffmpeg.exe") {
    Copy-Item "ffmpeg_bin\ffmpeg.exe" $dist
} elseif (Test-Path "ffmpeg.exe") {
    Copy-Item "ffmpeg.exe" $dist
} else {
    Write-Warning "ffmpeg.exe not found - app will download FFmpeg on first run"
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host "Folder: $dist" -ForegroundColor Green
Write-Host "Run:    $dist\IPTV-Overlay-Server.exe" -ForegroundColor Green
