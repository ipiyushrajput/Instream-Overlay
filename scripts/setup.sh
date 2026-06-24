#!/usr/bin/env bash
# One-time setup for the Instream Overlay project.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ">> Checking ffmpeg…"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found; installing (needs sudo/apt on Debian/Ubuntu)…"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y --no-install-recommends ffmpeg
  else
    echo "Please install ffmpeg manually for your platform." >&2
    exit 1
  fi
fi
ffmpeg -version | head -1

echo ">> Python backend deps…"
cd "$ROOT/backend"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt
deactivate

echo ">> Frontend deps…"
cd "$ROOT/frontend"
if command -v npm >/dev/null 2>&1; then
  npm install
else
  echo "npm not found; install Node 18+ to build the frontend." >&2
fi

echo ">> Done. See README.md for how to run the demo."
