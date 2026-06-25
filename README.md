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

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | base used when rewriting manifests |
| `OVERLAY_BUFFER_SEGMENTS` | `3` | how far behind live we hold the output |
| `OVERLAY_MAX_WORKERS` | `4` | concurrent ffmpeg transcodes |
| `OVERLAY_DATA_DIR` | `/tmp/instream-overlay-data` | overlaid segments + uploads |
| `OVERLAY_LOG_LEVEL` | `INFO` | set `DEBUG` to log the full ffmpeg command per transcode |
| `OVERLAY_VERIFY_TLS` | `0` | set `1` to verify origin TLS certs |

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
