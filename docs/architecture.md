# Architecture

## The core idea (how Transmit works, and how we replicate it)

A live HLS stream is a **master manifest** listing variant (resolution)
renditions, each pointing at a **child manifest** that lists `.ts` segments with
durations and `#EXT-X-PROGRAM-DATE-TIME` (PDT) wall-clock stamps. A live child
manifest is a sliding window: old segments drop off the top, new ones append at
the bottom.

To put an overlay on part of the stream **without re-encoding everything**, we:

1. **Mirror the manifests** on our own server. Players fetch our master and our
   child manifests instead of the origin's.
2. For every segment *not* inside an overlay window, our child manifest just
   points at the **origin's absolute segment URL** — pure pass-through, zero
   processing, zero cost.
3. For segments *inside* an overlay window, we download the original segment,
   **burn the overlay in with ffmpeg** (matched to that variant's codec), host
   the result on our server, and reference *our* segment in the manifest.
4. The overlaid run is bracketed by **`#EXT-X-DISCONTINUITY`** on entry and exit,
   because it's a fresh encode with its own decoder timeline. PDT and durations
   are preserved so the splice is seamless.

This is exactly the structure in the real Transmit manifest: origin segments via
CloudFront absolute URLs, then `#EXT-X-DISCONTINUITY` + overlaid segments served
from Transmit's own host, then `#EXT-X-DISCONTINUITY` + origin resumes.

## Components

```
backend/app/
  config.py        runtime config (buffer depth, workers, paths, public URL)
  models.py        Channel / VariantInfo / OverlayEvent + request bodies
  codecs.py        CODECS-string parsing (avc1.64101f -> High@3.1) + overlay
                   filter graph construction per overlay type
  manifest.py      hand-rolled m3u8 parse/render with full tag fidelity
  transcode.py     ffmpeg overlay burn for one segment, param-matched
  worker.py        async transcode pool + cache + DiscontinuityTracker
  store.py         in-memory channel/overlay state
  main.py          FastAPI: ingest, manifest serving + overlay injection,
                   segment serving, overlay CRUD, upload, status websocket
tools/gen_origin.py  ffmpeg-driven local multi-variant LIVE HLS origin
frontend/            React + Vite + hls.js operator console
```

## Request flow

**Ingest** (`POST /api/ingest`): fetch the origin master, parse each variant's
`RESOLUTION`, `CODECS`, `FRAME-RATE`, `BANDWIDTH`, and derive the encode params
(profile/level/width/height/fps/bitrate) used later to match the overlay
transcode. No re-probing of media needed.

**Master** (`GET /hls/{cid}/master.m3u8`): re-emit the variant `STREAM-INF`
lines verbatim, but point each at our child endpoint, carrying
`?h=&w=&codecs=&sessionStart=&tlSessionVer=` (mirrors Transmit's query params).

**Child** (`GET /manifest/{cid}/{sess}/{v}.m3u8`): on every request,
1. fetch + parse the origin child manifest,
2. **look-ahead pass** over the full window: for any segment whose PDT falls in
   an enabled overlay window, queue its transcode (so it's ready before it's
   exposed),
3. **hold the live edge back** by `OVERLAY_BUFFER_SEGMENTS` segments (the delay
   that buys transcode headroom),
4. for each exposed segment: if it's in an overlay window **and** its overlaid
   file is `READY`, swap in our segment URL and mark the overlay/origin
   transition with a discontinuity; otherwise emit the origin URL (fallback —
   never stall),
5. recompute `MEDIA-SEQUENCE` and `DISCONTINUITY-SEQUENCE` for the window.

**Segment** (`GET /segment/...`): serve the transcoded `.ts` from disk.

## The overlay transcode

`ffmpeg -copyts -i <origin_seg> -i <overlay.png> -filter_complex
"[1:v]scale=…[ov];[0:v][ov]overlay=…[v]" -map "[v]" -map 0:a?
-c:v libx264 -profile:v <p> -level <l> -pix_fmt yuv420p -r <fps>
-b:v <kbps>k -force_key_frames "expr:gte(t,0)" -c:a copy -f mpegts out.ts`

- **Matched encode** (profile/level/pix_fmt/fps/bitrate) so the overlaid segment
  is indistinguishable from the origin variant at the splice.
- **Forced IDR at t=0** → each overlaid segment is independently decodable, the
  requirement for a clean cut after a discontinuity.
- **Audio copied** → no re-encode drift, A/V stays locked.
- Overlay image **scaled per variant** (an L-band on 720p and on 360p are
  different pixel sizes).

## Hard problems and how they're handled

| Problem | Resolution |
|---------|-----------|
| Splice artifacts between origin & overlaid encodes | `#EXT-X-DISCONTINUITY` brackets + forced IDR + exact profile/level/pix_fmt/fps/res match |
| A/V desync in overlaid segments | copy audio, `-copyts`, identical durations |
| `DISCONTINUITY-SEQUENCE` correctness as window slides | `DiscontinuityTracker` counts only *injected* discontinuities that scroll out, added to the origin's own value |
| Overlay not ready when player reaches it | hold live edge back by N segments + look-ahead queueing; fall back to origin segment if still not ready |
| Real-time throughput (N variants × M segments) | async ffmpeg pool, transcode only requested variants, on-disk cache keyed by (channel, variant, seq, overlay) |
| Cross-origin segment loading in the browser | permissive CORS on our responses; origin must allow CORS (CloudFront does) |
| Variant fidelity | params derived once from master `CODECS`/`RESOLUTION`/`FRAME-RATE`, carried in child query params |

## Verified behavior (local origin simulator)

- Pass-through manifest decodes with **zero** ffmpeg errors.
- Overlaid 720p segment probes as **H.264 High @ 3.1, 1280×720, yuv420p, 30fps**
  — matching the variant; bottom-band pixels read pure red (overlay), top
  pixels unaffected.
- Live injection produces `#EXT-X-DISCONTINUITY` + our `/segment/...ts` +
  `#EXT-X-DISCONTINUITY` + origin resume, with `DISCONTINUITY-SEQUENCE`
  incrementing as the window slides.
- A 34-second decode straight through the overlay splice reports **0
  errors/corruption**.
- All three variants generate their own matched overlay segments.

## Roadmap (beyond the current build)

- SQLite persistence (swap `store.py`).
- Automatic "MomentAI"-style detection to trigger overlays from content signals.
- Interactive overlays (Transmit "Live Promotions": rollover / click-through).
- Per-variant transcode prioritization + adaptive shedding under load.
- SSAI/ad-decisioning + a demand-marketplace layer.
