# Install everything Instream Overlay needs on Windows, then build the frontend.
#
#   powershell -ExecutionPolicy Bypass -File deploy\setup-windows.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\setup-windows.ps1 -NoWinget
#
# Safe to re-run. It never overwrites an existing backend\.env, because that
# holds the database password.
param(
  [switch]$NoWinget,   # skip winget installs (no admin / already installed)
  [switch]$NoMysql     # skip the MySQL server install
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Backend  = Join-Path $RepoRoot "backend"
$Frontend = Join-Path $RepoRoot "frontend"

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# --- 1. system packages ----------------------------------------------------
if (-not $NoWinget) {
  Say "Installing system packages with winget"
  # --accept-* keeps it non-interactive; an already-installed package exits
  # non-zero, which is not a failure here.
  $pkgs = @("Gyan.FFmpeg", "Python.Python.3.12", "OpenJS.NodeJS.LTS")
  if (-not $NoMysql) { $pkgs += "Oracle.MySQL" }
  foreach ($p in $pkgs) {
    Write-Host "   $p"
    winget install --id $p --exact --silent `
      --accept-package-agreements --accept-source-agreements 2>$null
  }
  Write-Host "   NOTE: open a NEW terminal after this so PATH picks up ffmpeg/python/node."
} else {
  Say "Skipping winget (-NoWinget)"
}

foreach ($bin in @("ffmpeg", "ffprobe", "python", "node", "npm")) {
  if (-not (Get-Command $bin -ErrorAction SilentlyContinue)) {
    Write-Warning "$bin is not on PATH. Open a new terminal, or install it, then re-run."
  }
}

# --- 2. backend ------------------------------------------------------------
Say "Creating the backend virtualenv"
if (-not (Test-Path "$Backend\.venv")) { python -m venv "$Backend\.venv" }
& "$Backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Backend\.venv\Scripts\python.exe" -m pip install -r "$Backend\requirements.txt"

if (-not (Test-Path "$Backend\.env")) {
  Say "Creating backend\.env from .env.example"
  Copy-Item "$Backend\.env.example" "$Backend\.env"
  Write-Host "   EDIT IT: DB_PASS is empty and the backend will not start without it."
} else {
  Say "Keeping the existing backend\.env"
}

New-Item -ItemType Directory -Force -Path `
  "$Backend\data\segments", "$Backend\data\uploads", "$RepoRoot\logs" | Out-Null

# --- 3. frontend -----------------------------------------------------------
Say "Installing frontend dependencies"
Push-Location $Frontend
if (Test-Path "package-lock.json") {
  npm ci
  if ($LASTEXITCODE -ne 0) { npm install }
} else { npm install }

Say "Building the frontend"
# VITE_API_BASE is baked in at build time, so it must be a URL a BROWSER can
# reach — set it before running this if the browser is not on this machine.
$base = if ($env:VITE_API_BASE) { $env:VITE_API_BASE } else { "<unset, falling back to the default in src/api.js>" }
Write-Host "   VITE_API_BASE=$base"
npm run build
Pop-Location

Say "Done"
Write-Host @"

Next:
  1. Set the MySQL password in $Backend\.env (DB_PASS=...), or DB_ENABLED=0
     to run without MySQL.
  2. Set PUBLIC_BASE_URL in that file to the URL viewers reach this server on.
  3. Start it:
       cd $Backend
       .venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000
  4. Check it:  curl.exe -s http://127.0.0.1:8000/api/health

Full instructions: $RepoRoot\SETUP.md
"@
