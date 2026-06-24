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
scripts/    setup.sh
docs/       research + architecture
```

## Requirements

- `ffmpeg` / `ffprobe` on PATH (`sudo apt-get install -y ffmpeg`)
- Python 3.11+
- Node 18+

## Quick start

```bash
./scripts/setup.sh          # installs ffmpeg (if needed) + python/node deps
make demo                   # runs origin sim + backend + frontend together
```

Then open the operator console at **http://127.0.0.1:5173**. The master URL is
pre-filled with the local origin simulator. Click **Ingest**, then upload an
overlay PNG and **Schedule overlay**.

### Run the pieces manually

```bash
# 1) local origin (stands in for CloudFront; the real URL works too)
python3 tools/gen_origin.py --port 8100

# 2) backend
cd backend && . .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3) frontend
cd frontend && npm run dev
```

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
| POST | `/api/ingest` | register an origin master; probes variants |
| GET  | `/hls/{cid}/master.m3u8` | our mirrored master (play this) |
| GET  | `/manifest/{cid}/{sess}/{v}.m3u8` | child manifest w/ overlay injection |
| GET  | `/segment/{cid}/{v}/{ov}/{seq}.ts` | a transcoded overlay segment |
| POST | `/api/overlays/upload` | upload an overlay image |
| POST | `/api/overlays` | schedule overlay by absolute PDT window |
| POST | `/api/overlays/relative` | schedule overlay N seconds ahead of live edge |
| GET  | `/api/channels/{cid}/overlays` | list overlays |
| DELETE | `/api/overlays/{id}` | remove an overlay |
| WS   | `/ws` | per-segment transcode status |

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | base used when rewriting manifests |
| `OVERLAY_BUFFER_SEGMENTS` | `3` | how far behind live we hold the output |
| `OVERLAY_MAX_WORKERS` | `4` | concurrent ffmpeg transcodes |
| `OVERLAY_DATA_DIR` | `/tmp/instream-overlay-data` | overlaid segments + uploads |
