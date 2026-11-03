# Instream Overlay

A Transmit.Live-style system that burns **in-stream overlays** (L-band, lower
third, banner, full-frame sponsor graphics) into a **live HLS stream** *without
transcoding the whole stream*. Only the segments inside an operator-selected
overlay window get transcoded; every other segment is served straight from the
origin via its absolute URL. Our server mirrors the master + child manifests and
splices the overlaid segments in between `#EXT-X-DISCONTINUITY` markers,
preserving `#EXT-X-PROGRAM-DATE-TIME`, durations and sequence numbers so playback
stays smooth as the overlay "pops in and out".

See [`docs/research/transmit-live.md`](docs/research/transmit-live.md) for the
research this is modeled on and [`docs/architecture.md`](docs/architecture.md)
for how it works.

## What's here

```
backend/    FastAPI server: ingest + manifest mirror/rewrite + ffmpeg overlay
            transcode pool + overlaid-segment cache/serving + status websocket
frontend/   React + Vite + hls.js operator console (player, overlay controls)
tools/      gen_origin.py — local multi-variant LIVE HLS origin simulator
deploy/     setup-ubuntu.sh, setup-windows.ps1, systemd unit, nginx config
docs/       research + architecture
```

## Requirements

- `ffmpeg` / `ffprobe` on PATH
- Python 3.11+
- Node 18+ (Vite 5)
- MySQL 8 — optional, `DB_ENABLED=0` runs in memory

## Quick start

**[`SETUP.md`](SETUP.md) has the full Ubuntu and Windows instructions**,
including production deployment with systemd + nginx. The short version:

```bash
# Ubuntu — installs system packages, the venv and the frontend build
bash deploy/setup-ubuntu.sh
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File deploy\setup-windows.ps1
```

Then set `DB_PASS` and `PUBLIC_BASE_URL` in `backend/.env` (copied from
`.env.example`; the backend refuses to start without `DB_PASS` unless
`DB_ENABLED=0`) and run the pieces:

```bash
# Ubuntu
python3 tools/gen_origin.py --port 8100                    # 1) local origin
cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000   # 2)
cd frontend && npm run dev                                 # 3) console
```

```powershell
# Windows
python tools\gen_origin.py --port 8100
cd backend; .venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000
cd frontend; npm run dev
```

Then open the operator console at **http://127.0.0.1:5173**. Click **Ingest**,
then upload an overlay PNG and **Schedule overlay**.

> Against the local simulator, add `OVERLAY_BUFFER_SEGMENTS=2`: the simulator's
> live window is only 6 segments deep and the default hold-back of 6 consumes all
> of it, leaving an output manifest with no segments.

In production the frontend is built (`npm run build`) and served by nginx, not
by `npm run dev` — see [`SETUP.md`](SETUP.md#production-deployment-on-ubuntu).
`VITE_API_BASE` is baked into the bundle at build time and must be a URL the
browser can reach.

### Tests

```bash
cd backend && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest -q
```

No database or ffmpeg needed.

### Use a real origin

Point the console (or `POST /api/ingest`) at any live HLS master, e.g. your
CloudFront URL:

```bash
curl -X POST http://127.0.0.1:8000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"master_url":"https://your-cdn.example.com/Stream.m3u8","name":"live"}'
```

> Note: a sandboxed/blocked network can't reach external origins, so end-to-end
> testing in such an environment uses the local simulator. On your own machine,
> point it at the real CloudFront/transmit URL.

## Key API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/ingest` | register a channel from an origin master; probes variants |
| GET  | `/api/channels` | list channels |
| GET  | `/api/channels/{cid}` | view a channel |
| PUT  | `/api/channels/{cid}` | update name / change origin (re-probes) |
| GET  | `/api/channels/{cid}/status` | live edge, buffer, `min_lead_seconds`, active overlays |
| DELETE | `/api/channels/{cid}` | **stop ingestion** — drops channel, overlays, timelines |
| GET  | `/hls/{cid}/master.m3u8` | our mirrored master (play this) |
| GET  | `/manifest/{cid}/{sess}/{v}.m3u8` | child manifest w/ overlay injection |
| GET  | `/segment/{cid}/{v}/{ov}/{seq}.ts` | a transcoded overlay segment (referenced **relatively**) |
| POST | `/api/overlays/upload` | upload an overlay image |
| POST | `/api/overlays` | schedule overlay by absolute PDT window |
| POST | `/api/overlays/relative` | schedule overlay N seconds ahead (clamped to `min_lead_seconds`) |
| GET  | `/api/channels/{cid}/overlays` | list overlays with `status` (scheduled/active/completed/expired) + `injected_count` |
| DELETE | `/api/overlays/{id}` | remove an overlay |
| GET  | `/api/channels/{cid}/debug` | per-segment PDT / coverage / transcode status |
| GET  | `/api/channels/{cid}/insights` | viewer sessions + overlay deliveries for a channel |
| GET  | `/api/health` | database / ffmpeg / ffprobe / free disk; **503** when degraded |
| WS   | `/ws` | per-segment transcode + overlay lifecycle events |

### Playback correctness (how the live playlist stays valid)

Each segment's fate (origin vs overlaid) is **frozen the first time it is
published** and never changes on reload — satisfying the HLS rule that a
segment at a given Media Sequence Number is immutable. The live edge **waits**
for an overlay transcode to be ready before publishing that segment (rather than
publishing origin and swapping later), and `EXT-X-DISCONTINUITY-SEQUENCE` is
incremented as discontinuities scroll out, per RFC 8216. Overlay segments are
referenced with **relative** URLs (`/segment/…`); origin segments keep their
absolute origin URLs. This is what eliminates the `levelParsingError` /
buffering you get when a manifest mutates already-published segments.

## Configuration

Everything comes from the environment. `backend/.env` is loaded at startup if
present (real environment variables win over it), so a systemd unit or container
can override without editing the file. **[`backend/.env.example`](backend/.env.example)
is the authoritative list** — copy it to `backend/.env` and edit. `.env` is
gitignored and must never be committed.

| Var | Default | Meaning |
|-----|---------|---------|
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | the address a **viewer** reaches; every manifest/segment URL is built from it |
| `OVERLAY_BUFFER_SEGMENTS` | `6` | how far behind live we hold the output (the transcode headroom). **Must stay below the origin's window depth** — at or above it, the hold-back consumes the whole window and the output manifest comes back empty |
| `OVERLAY_HEVC_EXTRA_BUFFER` | `3` | extra hold-back when the origin is HEVC (libx265 is heavier) |
| `OVERLAY_MAX_WORKERS` | `6` | concurrent ffmpeg transcodes; enough to run all variants of a segment in parallel |
| `OVERLAY_HWACCEL` | `qsv` | `none` \| `qsv` \| `nvenc`; a failed hardware encode retries in software |
| `OVERLAY_ENCODER_PRESET` | `ultrafast` | x264/x265 preset for the squeezed overlay segments |
| `OVERLAY_ENCODER_CRF` | `28` | encode quality for overlay segments |
| `OVERLAY_ENCODER_THREADS` | `1` | per-encode thread cap (`0` = let ffmpeg choose) |
| `OVERLAY_DATA_DIR` | `backend/data` | overlaid segments + uploads; must persist across reboots |
| `OVERLAY_SEGMENT_RETENTION` | `1800` | transcoded segments older than this (seconds) are swept |
| `OVERLAY_RETENTION_INTERVAL` | `300` | how often the sweeper runs (seconds) |
| `OVERLAY_MIN_FREE_DISK_MB` | `2048` | free space below which `/api/health` reports degraded |
| `OVERLAY_VERIFY_TLS` | `1` | verify origin TLS certs; set `0` only for a chain this host cannot validate |
| `OVERLAY_LOG_LEVEL` | `INFO` | set `DEBUG` to log the full ffmpeg command per transcode |
| `OVERLAY_LOG_DIR` | `<repo>/logs` | rotating `backend.log` location |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | absolute paths if they are not on the service's PATH |
| `DB_ENABLED` | `1` | set `0` to run purely in-memory (no MySQL) |
| `DB_HOST` / `DB_PORT` | `localhost` / `3306` | MySQL connection |
| `DB_USER` / `DB_NAME` | `root` / `instream_overlay` | MySQL user + database |
| `DB_PASS` | **none — required** | no default on purpose; the app stops at startup and names it if unset |

## Database (MySQL)

Channels and overlays are persisted to MySQL so registered channels survive
restarts. The database (`instream_overlay`) and its tables are created
automatically on first run, so a user granted `ALL PRIVILEGES ON
instream_overlay.*` is enough. If MySQL is unreachable the backend logs a
warning and continues in-memory.

> On Ubuntu, `root@localhost` authenticates over the unix socket rather than
> with a password, so the backend cannot log in as root over TCP. Create a
> dedicated user — see [`SETUP.md`](SETUP.md#3-mysql).

The four overlay types are **L-band**,
**top_band**, **bottom_band**, and **pip**, each rendered as an animated
squeeze-back (the main video is scaled into a pocket while the band/ad art
shows in the freed area), encoded to match the origin codec (HEVC or H.264).

## Debugging "my overlay isn't showing"

The backend logs each step under the `overlay.*` loggers — to the uvicorn
console **and** to a rotating file at **`logs/backend.log`** (repo root; override
with `OVERLAY_LOG_DIR`). uvicorn's own request/error logs are captured there too.
Watch for:

- `ingested channel=… variants=N` and per-variant codec/profile/level.
- `overlay created (relative) … window=… origin_has_pdt=True/False`. If
  **`origin_has_pdt=False`**, your origin omits `#EXT-X-PROGRAM-DATE-TIME`, and
  overlay matching (which is wall-clock based) can't work — you'll also get a
  loud `WARNING`.
- `queued transcode …` → `transcoded … in Nms (bytes)` on success, or
  `transcode FAILED …` with the **ffmpeg stderr** on failure (codec mismatch,
  fMP4/CMAF segments, unreachable origin segment, etc.).
- `child vN: … covered=C overlaid=O waiting=W`. If `covered=0`, the log prints
  the exposed PDT range vs your overlay windows so you can see the mismatch.

There's also a JSON **debug endpoint** that shows, for each origin segment, its
PDT, which overlay covers it, and its transcode status + any error:

```bash
curl "http://127.0.0.1:8000/api/channels/<channel_id>/debug?variant_index=0" | jq
```

Note: because we hold the output `OVERLAY_BUFFER_SEGMENTS` behind the live edge,
a freshly scheduled overlay near the live edge takes roughly
`buffer × segment_duration` seconds to surface in the output — give it ~20s.
