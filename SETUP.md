# Setup and deployment

Instream Overlay (**TV Plus STUDIO**) on **Ubuntu** and on **Windows**. Every
step below gives the command for both; run the column that matches the machine
you are on.

- [What you are installing](#what-you-are-installing)
- [1. Prerequisites](#1-prerequisites)
- [2. Get the code](#2-get-the-code)
- [3. MySQL](#3-mysql)
- [4. Configure `backend/.env`](#4-configure-backendenv)
- [5. Backend](#5-backend)
- [6. Frontend](#6-frontend)
- [7. Verify](#7-verify)
- [Production deployment on Ubuntu](#production-deployment-on-ubuntu)
- [Running as a service on Windows](#running-as-a-service-on-windows)
- [Opening the firewall](#opening-the-firewall)
- [Updating a deployment](#updating-a-deployment)
- [Hardware-accelerated encoding](#hardware-accelerated-encoding)
- [Troubleshooting](#troubleshooting)

## What you are installing

| Piece | Default port | What it is |
|---|---|---|
| Backend | 8000 | FastAPI: manifest mirror/rewrite, ffmpeg overlay transcode pool, segment serving, status websocket |
| Frontend | 5173 (dev) / 80 (nginx) | React + Vite + hls.js operator console |
| MySQL | 3306 | channels, overlays, insights (optional — see `DB_ENABLED`) |
| Origin simulator | 8100 | `tools/gen_origin.py`, a local live HLS origin for testing |

The backend does **not** serve the frontend. In development Vite serves it; in
production nginx serves the built files and proxies the backend.

### Scripted install

Both scripts are idempotent and never overwrite an existing `backend/.env`.

```bash
# Ubuntu
bash deploy/setup-ubuntu.sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File deploy\setup-windows.ps1
```

They cover steps 1, 5 and 6. You still have to do step 3 (MySQL user) and
step 4 (`.env`) yourself, because those involve a password. The rest of this
document is the manual version, and the reference for what the scripts do.

---

## 1. Prerequisites

| Need | Why |
|---|---|
| `ffmpeg` + `ffprobe` on PATH | the overlay encode; the backend reports itself degraded without them |
| Python 3.11+ | backend |
| Node 18+ | frontend build (Vite 5 requires it) |
| MySQL 8 | persistence; skippable with `DB_ENABLED=0` |

### Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg python3 python3-venv python3-pip \
    build-essential python3-dev pkg-config curl git

# Node 18+ — Ubuntu's own nodejs package is too old for Vite 5.
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Check
ffmpeg -version | head -1 && python3 --version && node -v
```

Ubuntu 24.04 ships Python 3.12 and 22.04 ships 3.10. If you are on 22.04 and
want 3.11:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev
# then use python3.11 instead of python3 when creating the venv
```

### Windows

```powershell
winget install --id Gyan.FFmpeg --exact
winget install --id Python.Python.3.12 --exact
winget install --id OpenJS.NodeJS.LTS --exact
```

Open a **new** terminal afterwards so PATH picks them up, then check:

```powershell
ffmpeg -version | Select-Object -First 1
python --version
node -v
```

If `ffmpeg` is not found, the winget package did not land on PATH — add its
`bin` directory manually:

```powershell
# Find it, then add to PATH for this session (use the System settings UI to persist)
Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe |
  Select-Object -First 1 -ExpandProperty DirectoryName
$env:PATH += ";C:\path\from\above"
```

---

## 2. Get the code

### Ubuntu

```bash
sudo mkdir -p /opt
sudo git clone https://github.com/ipiyushrajput/Instream-Overlay.git /opt/instream-overlay
cd /opt/instream-overlay
```

For a development checkout, `~/Instream-Overlay` is fine; `/opt` is only the
convention the systemd unit and nginx config below assume.

### Windows

```powershell
git clone https://github.com/ipiyushrajput/Instream-Overlay.git C:\instream-overlay
cd C:\instream-overlay
```

---

## 3. MySQL

Skip this section entirely and set `DB_ENABLED=0` in step 4 if you do not want
persistence — the backend then keeps channels and overlays in memory and loses
them on restart.

### Ubuntu

```bash
sudo apt-get install -y mysql-server
sudo systemctl enable --now mysql
```

On Ubuntu, `root@localhost` authenticates with the unix socket, not a password,
so the backend cannot log in as root over TCP. Create a dedicated user instead:

```bash
sudo mysql <<'SQL'
CREATE DATABASE IF NOT EXISTS instream_overlay CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'overlay'@'localhost' IDENTIFIED BY 'CHANGE-ME-strong-password';
GRANT ALL PRIVILEGES ON instream_overlay.* TO 'overlay'@'localhost';
FLUSH PRIVILEGES;
SQL
```

`ALL PRIVILEGES ON instream_overlay.*` is enough: the backend runs
`CREATE DATABASE IF NOT EXISTS instream_overlay` on startup and that grant
covers creating that one schema, so it works whether or not you pre-created it.

Confirm the credentials work before touching `.env`:

```bash
mysql -h 127.0.0.1 -u overlay -p -e "SELECT 1"
```

### Windows

```powershell
winget install --id Oracle.MySQL --exact
```

The installer asks for a root password. Then, from "MySQL 8.0 Command Line
Client" or with `mysql.exe` on PATH:

```sql
CREATE DATABASE IF NOT EXISTS instream_overlay CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'overlay'@'localhost' IDENTIFIED BY 'CHANGE-ME-strong-password';
GRANT ALL PRIVILEGES ON instream_overlay.* TO 'overlay'@'localhost';
FLUSH PRIVILEGES;
```

---

## 4. Configure `backend/.env`

`backend/.env.example` documents every setting. Copy it and edit:

```bash
# Ubuntu
cp backend/.env.example backend/.env
chmod 600 backend/.env        # it holds a password
nano backend/.env
```

```powershell
# Windows
Copy-Item backend\.env.example backend\.env
notepad backend\.env
```

The three that matter on a fresh install:

```ini
# Required. The backend refuses to start without it (unless DB_ENABLED=0).
DB_PASS=the-password-you-just-set
DB_USER=overlay

# The address a VIEWER reaches, not the one you bind to. Every manifest,
# rendition and segment URL is built from it, so 127.0.0.1 here means players
# on other machines get URLs pointing at themselves.
PUBLIC_BASE_URL=http://YOUR_SERVER_IP:8000
```

`.env` is gitignored. Never commit it.

> **If your origin uses a certificate this host cannot validate**, add
> `OVERLAY_VERIFY_TLS=0`. It is on by default now; leaving it off is how a
> stream gets substituted in transit, so treat that as temporary.

> **`OVERLAY_HWACCEL` defaults to `qsv` in code** but to `none` in
> `.env.example`. On a server with no Intel Quick Sync, keep the `none` from the
> example — a failed hardware encode is retried in software, but you pay for the
> failed attempt on every segment. See
> [Hardware-accelerated encoding](#hardware-accelerated-encoding).

---

## 5. Backend

### Ubuntu

```bash
cd /opt/instream-overlay/backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# run it
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Windows

```powershell
cd C:\instream-overlay\backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

# run it
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000
```

A clean start logs the resolved configuration. If `DB_PASS` is missing it
stops immediately and names what to set — that is deliberate, not a crash.

Logs go to the console **and** to `logs/backend.log` at the repo root
(rotating, 5 files × 5 MB). Override with `OVERLAY_LOG_DIR`.

### Tests

```bash
# Ubuntu
cd backend && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest -q
```

```powershell
# Windows
cd backend
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

They need no database and no ffmpeg (`conftest.py` forces `DB_ENABLED=0` and
`OVERLAY_HWACCEL=none`).

## 6. Frontend

`VITE_API_BASE` is **baked into the bundle at build time**, so it must be a URL
the *browser* can reach. An unset or empty value falls back to the default in
`frontend/src/api.js`, so always set it explicitly for a deployment.

### Development (both platforms)

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
```

Point it at a backend somewhere else with an env file:

```bash
echo "VITE_API_BASE=http://YOUR_SERVER_IP:8000" > frontend/.env.local
```

### Production build

```bash
# Ubuntu — behind nginx on port 80, same origin as the console
cd /opt/instream-overlay/frontend
npm ci
VITE_API_BASE=http://YOUR_SERVER_IP npm run build     # -> frontend/dist
```

```powershell
# Windows
cd C:\instream-overlay\frontend
npm ci
$env:VITE_API_BASE="http://YOUR_SERVER_IP:8000"
npm run build
```

`npm run dev` is **not** needed once you have built — nginx serves `dist/`.
`npm run preview` serves the build on 4173 if you want to check it without
nginx.

## 7. Verify

```bash
# Ubuntu
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

```powershell
# Windows
curl.exe -s http://127.0.0.1:8000/api/health | python -m json.tool
```

`ok: true` means database, ffmpeg, ffprobe and free disk are all fine.
Anything broken is listed in `degraded` and the endpoint answers **503**, so a
load balancer can act on the status code alone:

```json
{ "ok": false, "degraded": ["disk", "ffmpeg"], "checks": { "...": {} } }
```

Then open the console, ingest a channel, and play the mirrored master:

```bash
# a local origin to test against, if you have no live source handy
python3 tools/gen_origin.py --port 8100

curl -X POST http://127.0.0.1:8000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"master_url":"http://127.0.0.1:8100/master.m3u8","name":"test"}'

# play this
curl -s http://127.0.0.1:8000/hls/<channel_id>/master.m3u8
```

> **Testing against the local simulator?** `tools/gen_origin.py` exposes a
> **6-segment** live window, and `OVERLAY_BUFFER_SEGMENTS` defaults to **6** — so
> the hold-back consumes the whole window and the child manifest comes back with
> headers and **no segments at all**. That is arithmetic, not a fault. For local
> testing only, run the backend with a smaller buffer:
>
> ```bash
> OVERLAY_BUFFER_SEGMENTS=2 .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
> ```
>
> The same arithmetic applies in production: **`OVERLAY_BUFFER_SEGMENTS` must stay
> below your origin's window depth**, or your output window shrinks to nothing
> and players rebuffer. Check the depth with
> `curl -s <origin child>.m3u8 | grep -c EXTINF`; the default of 6 assumes an
> origin carrying rather more than that.

---

## Production deployment on Ubuntu

Layout the templates assume: checkout at `/opt/instream-overlay`, service user
`overlay`, backend on `127.0.0.1:8000`, nginx public on `:80`.

### 1. Service user and ownership

Running the backend as root means an ffmpeg command built from an uploaded
filename runs as root. Don't.

```bash
sudo useradd --system --home /opt/instream-overlay --shell /usr/sbin/nologin overlay
sudo chown -R overlay:overlay /opt/instream-overlay
sudo chmod 600 /opt/instream-overlay/backend/.env
sudo chown overlay:overlay /opt/instream-overlay/backend/.env
```

The backend writes to `backend/data/` (transcoded segments, uploaded art) and
`logs/`; both must be writable by `overlay`. If you move the data elsewhere,
set `OVERLAY_DATA_DIR` and add that path to `ReadWritePaths` in the unit.

### 2. systemd

```bash
sudo cp /opt/instream-overlay/deploy/systemd/instream-overlay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now instream-overlay

systemctl status instream-overlay
journalctl -u instream-overlay -f
```

Edit the unit first if your paths or user differ. It binds to 127.0.0.1 because
nginx fronts it — drop `--host 127.0.0.1` to `0.0.0.0` if you are not using
nginx.

### 3. nginx

```bash
sudo apt-get install -y nginx
sudo cp /opt/instream-overlay/deploy/nginx/instream-overlay.conf \
        /etc/nginx/sites-available/instream-overlay
sudo sed -i 's/YOUR_HOST/13.238.93.19/' /etc/nginx/sites-available/instream-overlay
sudo ln -sf /etc/nginx/sites-available/instream-overlay /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default      # otherwise it wins on :80
sudo nginx -t && sudo systemctl reload nginx
```

With nginx on port 80 the console and the API share one origin, so set both
without `:8000`:

```ini
# backend/.env
PUBLIC_BASE_URL=http://13.238.93.19
```
```bash
# frontend rebuild
cd /opt/instream-overlay/frontend && VITE_API_BASE=http://13.238.93.19 npm run build
sudo systemctl restart instream-overlay
```

The config handles the things a live HLS service needs and a default nginx gets
wrong: manifests proxied with `proxy_buffering off` and `no-store` (a buffered
playlist hands the player a live edge that has already moved), segments cached
for 10 minutes, `/ws` upgraded with a 1-hour read timeout, `client_max_body_size
32m` for overlay art, and `try_files ... =404` so a missing `dist/` gives a 404
instead of a 500 redirect cycle.

**If you get a 500 from nginx,** first check nothing else is already running on
:80 — more than one nginx instance, or an old config in `sites-enabled`, is the
usual cause:

```bash
sudo ss -ltnp | grep ':80'
ls -l /etc/nginx/sites-enabled/
sudo tail -40 /var/log/nginx/instream-overlay.error.log
```

### 4. HTTPS (optional)

Needs a domain pointing at the host; certbot cannot issue for a bare IP.

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d studio.example.com
```

Then update both URLs to `https://studio.example.com` and rebuild the frontend —
a page served over HTTPS cannot fetch `http://` manifests.

---

## Running as a service on Windows

There is no built-in equivalent of systemd for this; use NSSM.

```powershell
winget install --id NSSM.NSSM --exact

nssm install InstreamOverlay C:\instream-overlay\backend\.venv\Scripts\uvicorn.exe `
  "app.main:app --host 0.0.0.0 --port 8000"
nssm set InstreamOverlay AppDirectory C:\instream-overlay\backend
nssm set InstreamOverlay Start SERVICE_AUTO_START
nssm start InstreamOverlay

nssm status InstreamOverlay
# logs still land in C:\instream-overlay\logs\backend.log
```

To serve the frontend, either install nginx for Windows and adapt
`deploy/nginx/instream-overlay.conf` (change `root` to
`C:/instream-overlay/frontend/dist`, forward slashes), or just run
`npm run preview -- --host --port 80`.

## Opening the firewall

### Ubuntu (ufw)

```bash
sudo ufw allow 22/tcp          # keep SSH before enabling, or you lock yourself out
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp         # only if you set up HTTPS
# only if you expose the backend directly instead of via nginx:
sudo ufw allow 8000/tcp
sudo ufw enable
sudo ufw status verbose
```

On a cloud VM the instance's own security group has to allow the same ports —
AWS, for instance, blocks them regardless of ufw.

### Windows

```powershell
# Run as Administrator
New-NetFirewallRule -DisplayName "Instream Overlay HTTP 80" -Direction Inbound `
  -Protocol TCP -LocalPort 80 -Action Allow

New-NetFirewallRule -DisplayName "Instream Overlay backend 8000" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow

New-NetFirewallRule -DisplayName "Instream Overlay Vite 5173" -Direction Inbound `
  -Protocol TCP -LocalPort 5173 -Action Allow

# check / remove
Get-NetFirewallRule -DisplayName "Instream Overlay*" | Format-Table DisplayName,Enabled
Remove-NetFirewallRule -DisplayName "Instream Overlay Vite 5173"
```

## Updating a deployment

### Ubuntu

```bash
cd /opt/instream-overlay
sudo -u overlay git pull origin main

cd backend && sudo -u overlay .venv/bin/pip install -r requirements.txt
cd ../frontend && sudo -u overlay npm ci && \
  sudo -u overlay env VITE_API_BASE=http://13.238.93.19 npm run build

sudo systemctl restart instream-overlay
curl -s http://127.0.0.1:8000/api/health
```

A restart drops in-flight transcodes and the published timelines, so players
reload the playlist; do it between overlay windows. Channels and overlays
survive in MySQL.

### Windows

```powershell
cd C:\instream-overlay
git pull origin main
cd backend; .venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..\frontend; npm ci; npm run build
nssm restart InstreamOverlay
```

## Hardware-accelerated encoding

Software `libx264`/`libx265` is the default and always works. On a slow CPU the
overlay transcode is the bottleneck, and `OVERLAY_HWACCEL` offloads it. A
hardware encode that fails at runtime is retried in software automatically, so a
wrong setting degrades rather than breaks — but you pay for the failed attempt
on every segment, so verify before leaving it on.

```bash
# Intel Quick Sync (OVERLAY_HWACCEL=qsv)
sudo apt-get install -y intel-media-va-driver-non-free vainfo
vainfo | grep -i h264        # must list an encode entrypoint
sudo usermod -aG render,video overlay

# NVIDIA (OVERLAY_HWACCEL=nvenc)
nvidia-smi                   # driver present?
ffmpeg -hide_banner -encoders | grep nvenc

# confirm your ffmpeg has the encoder at all
ffmpeg -hide_banner -encoders | grep -E 'qsv|nvenc'
```

Ubuntu's `ffmpeg` package includes both. A virtualised cloud instance usually
has neither device — keep `OVERLAY_HWACCEL=none` there and raise
`OVERLAY_MAX_WORKERS` instead if you have cores to spare.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Backend exits at startup naming `DB_PASS` | `backend/.env` missing or `DB_PASS` empty | set it, or `DB_ENABLED=0` |
| `Access denied for user 'root'@'localhost'` | Ubuntu root MySQL uses the unix socket, not a password | create the `overlay` user in [step 3](#3-mysql) |
| `/api/health` → 503, `degraded: ["ffmpeg"]` | not on PATH, or not on the *service's* PATH | `which ffmpeg`; set `FFMPEG_BIN`/`FFPROBE_BIN` to absolute paths |
| `/api/health` → 503, `degraded: ["disk"]` | free space under `OVERLAY_MIN_FREE_DISK_MB` | the retention sweep runs every `OVERLAY_RETENTION_INTERVAL`; shorten `OVERLAY_SEGMENT_RETENTION` or add disk |
| Console loads, every API call fails | `VITE_API_BASE` baked in wrong, or backend unreachable from the browser | rebuild with the right value; check the firewall |
| Child manifest has headers but no `#EXTINF` segments | `OVERLAY_BUFFER_SEGMENTS` ≥ the origin's window depth, so the hold-back eats the whole window | lower it below `curl -s <origin child>.m3u8 \| grep -c EXTINF` |
| Player stalls, `levelParsingError` | manifests being buffered or cached by a proxy | use the supplied nginx config; manifests need `proxy_buffering off` + `no-store` |
| nginx 500 on every path | another nginx instance or a stale `sites-enabled` entry on :80 | `sudo ss -ltnp \| grep ':80'`, clear `sites-enabled`, `nginx -t` |
| Overlay never appears | origin has no `#EXT-X-PROGRAM-DATE-TIME` | check for `origin_has_pdt=False` in `logs/backend.log`; overlay matching is wall-clock based and needs PDT |
| Overlay appears ~20s late | by design: output is held `OVERLAY_BUFFER_SEGMENTS` behind the live edge | that is the transcode headroom; see `min_lead_seconds` in `/api/channels/{id}/status` |
| Transcode failures under load | too many parallel encodes for the CPU | lower `OVERLAY_MAX_WORKERS`, raise `OVERLAY_ENCODER_CRF`, or enable hardware encode |
| `npm run build` fails on an old Node | Vite 5 needs Node 18+ | `node -v`, then install Node 20 as in [step 1](#1-prerequisites) |
| WebSocket drops every minute | proxy read timeout too short | `/ws` needs a long `proxy_read_timeout` (the supplied config sets 3600s) |

Per-segment diagnostics — PDT, which overlay covers each segment, transcode
status and any ffmpeg error:

```bash
curl -s "http://127.0.0.1:8000/api/channels/<channel_id>/debug?variant_index=0"
```

Set `OVERLAY_LOG_LEVEL=DEBUG` to log the full ffmpeg command for every
transcode.

---

## Before you expose this publicly

The backend currently has **no authentication** and `CORS allow_origins=["*"]`,
so anyone who can reach port 80/8000 can ingest channels, upload art and
schedule overlays. Until that is addressed, restrict access at the network
layer — a security group or ufw rule limited to your own addresses:

```bash
sudo ufw allow from YOUR.OFFICE.IP.ADDR to any port 80 proto tcp
```

Also: if the MySQL password was ever committed to a repository, rotate it. A
password in git history stays readable even after the file changes.
