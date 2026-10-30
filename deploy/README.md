# Deploying the Instream Overlay console

One nginx origin on **port 80** serves the built frontend and proxies the
backend, so the browser bundle carries no host or port and the same artefact
runs on Windows or Linux unchanged.

```
browser ──► nginx :80   ──┬──► frontend/dist           (static, SPA fallback)
                          └──► FastAPI 127.0.0.1:8000  (/api /hls /manifest
                                                        /rendition /segment
                                                        /uploads /ws)
```

The app owns the whole origin, so URLs carry no port: `http://13.238.93.19/`.

## 1. Prerequisites

- Python 3.11+, Node 18+
- **ffmpeg and ffprobe on `PATH`** (the overlay transcodes shell out to them)
- MySQL or MariaDB reachable from the backend

## 2. Build the frontend

Build with no `VITE_API_BASE`, so the bundle talks to whatever host served it.

```bash
cd frontend
npm ci
npm run build          # -> frontend/dist
```

To pre-fill the origin field on the Register page, build with
`VITE_DEFAULT_ORIGIN=https://your-cdn/master.m3u8`.

## 3. Configure and run the backend

`PUBLIC_BASE_URL` **must** be the address a viewer's player can reach — the
nginx entry point, not the loopback the app binds to. Every variant, rendition,
segment and upload URL in the manifests is built from it; get it wrong and the
master playlist loads while every child 404s.

| Variable | Value here | Purpose |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://13.238.93.19` | written into manifests |
| `DB_HOST` / `DB_PORT` | `localhost` / `3306` | MySQL |
| `DB_USER` / `DB_PASS` / `DB_NAME` | … / … / `instream_overlay` | MySQL |
| `OVERLAY_BUFFER_SEGMENTS` | `6` | output held behind live |
| `OVERLAY_HWACCEL` | `qsv`, `nvenc`, or `none` | GPU encode; `none` on most cloud VMs |
| `OVERLAY_MAX_WORKERS` | `6` | parallel ffmpeg jobs |

**Linux**

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export PUBLIC_BASE_URL=http://13.238.93.19
export OVERLAY_HWACCEL=none          # no Quick Sync on most cloud VMs
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Windows (PowerShell)**

```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PUBLIC_BASE_URL = "http://13.238.93.19"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Bind the backend to `127.0.0.1`, not `0.0.0.0` — only nginx should reach it.

## 4. nginx

Copy `deploy/nginx.conf` over your `nginx.conf` and change **one line** — the
`root` — to point at `frontend/dist` on this machine. Forward slashes work on
Windows too.

```bash
nginx -t                       # check the config
nginx -s reload                # or: start nginx
```

## 5. Open the port

`13.238.93.19` is a cloud instance, so the OS firewall is not the only gate:
**make sure inbound TCP 80 is allowed** in the instance's security group /
firewall, or the port is unreachable from outside however correct nginx is.

On Linux, binding port 80 needs root (`sudo nginx`) or
`setcap 'cap_net_bind_service=+ep'` on the binary.

## 6. Verify

```bash
curl http://13.238.93.19/api/health            # {"ok":true,...}
curl -I http://13.238.93.19/                   # 200, text/html
```

Then open `http://13.238.93.19/`, register a channel, and check that the
output master URL on the console page is an **`http://13.238.93.19/...`**
address. If it shows anything else, `PUBLIC_BASE_URL` is wrong.

## Notes

- **CORS**: the backend sends its own `Access-Control-Allow-Origin`. Do not add
  one in nginx for the proxied locations — two values make browsers reject the
  response.
- **HTTPS**: if you put this behind TLS later, serve the frontend and the API
  from the same HTTPS host and set `PUBLIC_BASE_URL=https://…`. A plain-HTTP
  origin inside an HTTPS page is blocked as mixed content.
- **sendfile**: left `off` for Windows, where nginx does not support it. Turn it
  on once you are on Linux.
