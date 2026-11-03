#!/usr/bin/env bash
#
# Install everything Instream Overlay needs on Ubuntu, then build the frontend.
#
#   bash deploy/setup-ubuntu.sh            # system packages + backend venv + frontend build
#   bash deploy/setup-ubuntu.sh --no-apt   # skip apt (no sudo / already installed)
#   bash deploy/setup-ubuntu.sh --no-mysql # skip the MySQL server package
#
# Safe to re-run: every step checks before it acts. It never overwrites an
# existing backend/.env, because that holds the database password.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
FRONTEND="$REPO_ROOT/frontend"

WITH_APT=1
WITH_MYSQL=1
for arg in "$@"; do
  case "$arg" in
    --no-apt)   WITH_APT=0 ;;
    --no-mysql) WITH_MYSQL=0 ;;
    -h|--help)  sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- 1. system packages ----------------------------------------------------
# ffmpeg/ffprobe do the overlay encode; python3-venv is a separate package on
# Ubuntu and python3 -m venv fails without it; build-essential + the dev headers
# are what the cryptography/PyMySQL wheels fall back to on architectures with no
# prebuilt wheel.
if [ "$WITH_APT" = 1 ]; then
  say "Installing system packages (sudo)"
  SUDO=""
  [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -y
  $SUDO apt-get install -y \
    ffmpeg \
    python3 python3-venv python3-pip \
    build-essential python3-dev default-libmysqlclient-dev pkg-config \
    curl ca-certificates git
  if [ "$WITH_MYSQL" = 1 ]; then
    $SUDO apt-get install -y mysql-server
    $SUDO systemctl enable --now mysql
  fi
  # Ubuntu's own nodejs is too old for Vite 5 (needs 18+), so use NodeSource
  # when the installed major version is below 18 or Node is missing entirely.
  node_major="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || true)"
  if [ -z "$node_major" ] || [ "$node_major" -lt 18 ]; then
    say "Installing Node.js 20 (found: ${node_major:-none}, need 18+)"
    curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO -E bash -
    $SUDO apt-get install -y nodejs
  fi
else
  say "Skipping apt (--no-apt)"
fi

# --- 2. backend ------------------------------------------------------------
say "Creating the backend virtualenv"
[ -d "$BACKEND/.venv" ] || python3 -m venv "$BACKEND/.venv"
"$BACKEND/.venv/bin/pip" install --upgrade pip
"$BACKEND/.venv/bin/pip" install -r "$BACKEND/requirements.txt"

if [ ! -f "$BACKEND/.env" ]; then
  say "Creating backend/.env from .env.example"
  cp "$BACKEND/.env.example" "$BACKEND/.env"
  chmod 600 "$BACKEND/.env"
  echo "   EDIT IT: DB_PASS is empty and the backend will not start without it."
else
  say "Keeping the existing backend/.env"
fi

mkdir -p "$BACKEND/data/segments" "$BACKEND/data/uploads" "$REPO_ROOT/logs"

# --- 3. frontend -----------------------------------------------------------
say "Installing frontend dependencies"
cd "$FRONTEND"
# npm ci is reproducible but needs the lockfile to match package.json.
if [ -f package-lock.json ]; then npm ci || npm install; else npm install; fi

say "Building the frontend"
# VITE_API_BASE is baked into the bundle at build time, so it must be the URL a
# BROWSER can reach — not 127.0.0.1, unless you only ever open it on this host.
echo "   VITE_API_BASE=${VITE_API_BASE:-<unset, falling back to the default in src/api.js>}"
npm run build

say "Done"
cat <<EOF

Next:
  1. Set the MySQL password in $BACKEND/.env (DB_PASS=...), or DB_ENABLED=0
     to run without MySQL.
  2. Set PUBLIC_BASE_URL in that file to the URL viewers reach this server on.
  3. Start it:
       cd $BACKEND && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
     or install the service unit: see deploy/systemd/instream-overlay.service
  4. Check it:  curl -s http://127.0.0.1:8000/api/health

Full instructions: $REPO_ROOT/SETUP.md
EOF
