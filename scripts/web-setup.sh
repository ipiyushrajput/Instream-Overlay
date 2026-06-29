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

# Best-effort: start a local MariaDB so channels persist (the app falls back to
# in-memory if this fails). Harmless if MariaDB isn't installed.
if command -v mariadbd >/dev/null 2>&1; then
  if [ ! -d /var/lib/mysql/mysql ]; then
    mariadb-install-db --user=root --datadir=/var/lib/mysql >/dev/null 2>&1 || true
  fi
  mkdir -p /run/mysqld || true
  if ! mariadb-admin --socket=/run/mysqld/mysqld.sock ping >/dev/null 2>&1; then
    nohup /usr/sbin/mariadbd --user=root --datadir=/var/lib/mysql \
      --socket=/run/mysqld/mysqld.sock >/tmp/mariadb.log 2>&1 &
    sleep 5
    mariadb --socket=/run/mysqld/mysqld.sock \
      -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_PASS:-Piyush@23}';
          CREATE DATABASE IF NOT EXISTS ${DB_NAME:-instream_overlay};" >/dev/null 2>&1 || true
  fi
fi

echo "instream-overlay: environment ready (ffmpeg=$(command -v ffmpeg || echo missing))"
