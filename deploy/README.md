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

`npm ci` reports audit "vulnerabilities" from transitive dev dependencies. They
do not affect the build or anything that runs in production. **Do not run
`npm audit fix --force`** to clear them — it will upgrade Vite across a major
version and break the build.

> **Do not run `npm run dev` on the server.** That starts the Vite dev server on
> :5173, which nothing here points at. nginx serves the built files straight
> from `frontend/dist`, so there is no Node process in production — only the
> backend, MySQL and nginx. Re-run `npm run build` after a frontend change;
> nginx picks the new files up on the next request with no reload, because
> `index.html` is served `no-cache` and asset filenames are content-hashed.
> `npm run dev` is for your own machine while editing the UI.

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

There are **two** gates on a cloud instance, and both must allow TCP 80:

1. **The cloud security group / firewall** — for `13.238.93.19` that is the
   EC2 instance's security group. Add an inbound rule: TCP, port 80, from
   `0.0.0.0/0` (or just your office range). Nothing below helps until this is
   open.
2. **The OS firewall**, below.

**Windows (PowerShell, as Administrator)**

```powershell
New-NetFirewallRule -DisplayName "Instream Overlay HTTP (80)" `
                    -Direction Inbound -Protocol TCP -LocalPort 80 `
                    -Action Allow -Profile Any
```

Check it, and check nginx is actually listening:

```powershell
Get-NetFirewallRule -DisplayName "Instream Overlay HTTP (80)"
Get-NetTCPConnection -LocalPort 80 -State Listen
```

If something else already holds port 80 (IIS is the usual culprit on Windows
Server), nginx will fail to bind. Find and stop it:

```powershell
Get-Process -Id (Get-NetTCPConnection -LocalPort 80 -State Listen).OwningProcess
Stop-Service W3SVC; Set-Service W3SVC -StartupType Disabled   # if it is IIS
```

To remove the rule later: `Remove-NetFirewallRule -DisplayName "Instream Overlay HTTP (80)"`.

**Linux**

```bash
sudo ufw allow 80/tcp                      # Ubuntu/Debian
sudo firewall-cmd --permanent --add-service=http && sudo firewall-cmd --reload  # RHEL
```

Binding port 80 on Linux also needs root (`sudo nginx`) or
`setcap 'cap_net_bind_service=+ep'` on the binary.

## 6. Verify

```bash
curl http://13.238.93.19/api/health            # {"ok":true,...}
curl -I http://13.238.93.19/                   # 200, text/html
```

Then open `http://13.238.93.19/`, register a channel, and check that the
output master URL on the console page is an **`http://13.238.93.19/...`**
address. If it shows anything else, `PUBLIC_BASE_URL` is wrong.

## Troubleshooting

**`/` returns 500 but `/api/health` works.**
The proxy is fine; nginx cannot find the frontend — proxied locations never
touch `root`, which is why only the static side fails. In the log it reads:

```
rewrite or internal redirection cycle while internally redirecting to "/index.html"
```

Work through these three, in order:

1. **Is there a build?** `npm ci` installs dependencies but produces no `dist`;
   `npm run build` is a separate step.
   ```powershell
   Test-Path C:\path\to\frontend\dist\index.html
   ```
2. **Is nginx reading the file you edited?** `nginx -t` prints the config path
   it actually loads. On a machine with more than one nginx, it is easy to edit
   a different copy.
3. **Did the reload take, and is only one nginx running?** `nginx -s reload` is
   unreliable on Windows, and a stale instance left bound to :80 goes on
   serving its own `root` while every edit appears to do nothing — proxied
   locations keep working the whole time, which makes it look like a static
   problem rather than a stale process.

   A healthy nginx is several processes (one master plus workers), so the count
   is not the tell; the binary path is. Check who actually owns the port:
   ```powershell
   Get-Process nginx | Select-Object Id, Path   # differing paths = two installs
   Get-Process -Id (Get-NetTCPConnection -LocalPort 80 -State Listen).OwningProcess |
       Select-Object Id, Path
   ```
   Then clear them all out and start one:
   ```powershell
   taskkill /F /IM nginx.exe    # every instance, master and workers
   start nginx
   ```

The error log names the file it tried:

```bash
# Linux
tail -n 50 /var/log/nginx/error.log
# Windows
type logs\error.log
```

Then check the build actually exists at the path in `root`:

```bash
# Linux
ls -l /opt/instream-overlay/frontend/dist/index.html
# Windows
dir C:\Piyush\POC\Instream-Overlay\frontend\dist\index.html
```

If it is missing, build it (`cd frontend && npm ci && npm run build`). If it is
there but nginx still cannot read it on Linux, the worker user needs traverse
permission on every parent directory — serving out of `/home/<user>/` is the
usual culprit. Put the build somewhere like `/opt/instream-overlay`, or:

```bash
sudo chmod o+x /home /home/<user>            # traverse
sudo chown -R root:www-data /opt/instream-overlay/frontend/dist
```

**Everything 404s after a correct build.** Check `root` has no trailing slash
and points at `dist` itself, not its parent.

## Notes

- **CORS**: the backend sends its own `Access-Control-Allow-Origin`. Do not add
  one in nginx for the proxied locations — two values make browsers reject the
  response.
- **HTTPS**: if you put this behind TLS later, serve the frontend and the API
  from the same HTTPS host and set `PUBLIC_BASE_URL=https://…`. A plain-HTTP
  origin inside an HTTPS page is blocked as mixed content.
- **sendfile**: left `off` for Windows, where nginx does not support it. Turn it
  on once you are on Linux.
