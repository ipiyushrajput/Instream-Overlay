#!/usr/bin/env bash
# Idempotent setup for Claude Code web sessions: ensures ffmpeg + project deps
# are present so the backend, origin simulator, and frontend can run/test.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    (sudo apt-get update -y && sudo apt-get install -y --no-install-recommends ffmpeg) \
      || (apt-get update -y && apt-get install -y --no-install-recommends ffmpeg) || true
  fi
fi

if [ ! -d "$ROOT/backend/.venv" ]; then
  python3 -m venv "$ROOT/backend/.venv"
  # shellcheck disable=SC1091
  source "$ROOT/backend/.venv/bin/activate"
  pip install --upgrade pip >/dev/null 2>&1 || true
  pip install -r "$ROOT/backend/requirements.txt" >/dev/null 2>&1 || true
  deactivate || true
fi

if [ ! -d "$ROOT/frontend/node_modules" ] && command -v npm >/dev/null 2>&1; then
  (cd "$ROOT/frontend" && npm install >/dev/null 2>&1) || true
fi

echo "instream-overlay: environment ready (ffmpeg=$(command -v ffmpeg || echo missing))"
