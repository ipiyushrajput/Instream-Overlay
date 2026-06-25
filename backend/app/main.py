"""FastAPI application: manifest mirror + overlay injection + operator API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from . import config, manifest
from .codecs import video_params_from_variant
from .models import (Channel, CreateOverlayRelativeRequest, CreateOverlayRequest,
                     IngestRequest, OverlayEvent, VariantInfo)
from .store import new_id, store
from .transcode import variant_video_params
from .worker import DiscontinuityTracker, Job, JobStatus, pool

# Per-(channel, variant) discontinuity-sequence trackers.
_trackers: dict[tuple, DiscontinuityTracker] = {}


def _tracker(channel_id: str, variant_index: int) -> DiscontinuityTracker:
    key = (channel_id, variant_index)
    if key not in _trackers:
        _trackers[key] = DiscontinuityTracker()
    return _trackers[key]


def _parse_pdt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


log = logging.getLogger("overlay.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    config.setup_logging()
    log.info("starting up: buffer=%d segs, workers=%d, verify_tls=%s, data=%s",
             config.BUFFER_SEGMENTS, config.MAX_TRANSCODE_WORKERS,
             config.VERIFY_TLS, config.DATA_DIR)
    app.state.http = httpx.AsyncClient(timeout=config.ORIGIN_TIMEOUT,
                                       follow_redirects=True,
                                       verify=config.VERIFY_TLS)
    pool.set_status_callback(_broadcast)
    pool.start()
    yield
    await pool.stop()
    await app.state.http.aclose()


app = FastAPI(title="Instream Overlay", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"], expose_headers=["*"],
)


# --- websocket status ------------------------------------------------------

_ws_clients: set[WebSocket] = set()


async def _broadcast(message: dict) -> None:
    dead = []
    for ws in list(_ws_clients):
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# --- helpers ---------------------------------------------------------------

async def _fetch_text(url: str) -> str:
    resp = await app.state.http.get(url)
    resp.raise_for_status()
    return resp.text


# --- ingest ----------------------------------------------------------------

@app.post("/api/ingest", response_model=Channel)
async def ingest(req: IngestRequest):
    try:
        text = await _fetch_text(req.master_url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Failed to fetch master: {exc}")

    if not manifest.is_master(text):
        # Single-variant playlist: synthesize one variant pointing at it.
        variants = [VariantInfo(index=0, origin_uri=req.master_url,
                                inf_line="#EXT-X-STREAM-INF:BANDWIDTH=2000000")]
    else:
        parsed = manifest.parse_master(text, req.master_url)
        if not parsed:
            raise HTTPException(400, "No variants found in master playlist")
        variants = []
        for idx, mv in enumerate(parsed):
            vp = video_params_from_variant(mv.codecs, mv.resolution,
                                           mv.frame_rate, mv.bandwidth)
            variants.append(VariantInfo(
                index=idx, origin_uri=mv.uri, inf_line=mv.inf_line,
                codecs=mv.codecs, resolution=mv.resolution,
                frame_rate=mv.frame_rate, bandwidth=mv.bandwidth,
                width=vp.width, height=vp.height, fps=vp.fps,
                profile=vp.profile, level=vp.level, pix_fmt=vp.pix_fmt,
                bitrate_kbps=vp.bitrate_kbps))

    channel = Channel(id=new_id(), name=req.name or "channel",
                      master_url=req.master_url, variants=variants)
    store.add_channel(channel)
    log.info("ingested channel=%s master=%s variants=%d", channel.id,
             req.master_url, len(variants))
    for v in variants:
        log.info("  variant v%s: %s codecs=%s profile=%s level=%s fps=%s "
                 "bitrate=%skbps origin=%s", v.index, v.resolution, v.codecs,
                 v.profile, v.level, v.fps, v.bitrate_kbps, v.origin_uri)
    return channel


@app.get("/api/channels")
async def list_channels():
    return store.list_channels()


@app.get("/api/channels/{channel_id}", response_model=Channel)
async def get_channel(channel_id: str):
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    return ch


@app.get("/api/channels/{channel_id}/status")
async def channel_status(channel_id: str):
    """Live-edge + buffer info for the operator console."""
    ch = store.get_channel(channel_id)
    if not ch or not ch.variants:
        raise HTTPException(404, "channel not found")
    text = await _fetch_text(ch.variants[0].origin_uri)
    pl = manifest.parse_media(text, ch.variants[0].origin_uri)
    edge = None
    if pl.segments:
        last = pl.segments[-1]
        pdt = _parse_pdt(last.pdt)
        if pdt:
            edge = (pdt + timedelta(seconds=last.duration)).isoformat()
    return {"channel_id": channel_id, "live_edge_pdt": edge,
            "buffer_segments": config.BUFFER_SEGMENTS,
            "segment_count": len(pl.segments)}


# --- overlays --------------------------------------------------------------

@app.post("/api/overlays/upload")
async def upload_overlay(file: UploadFile = File(...)):
    config.ensure_dirs()
    name = f"{new_id()}_{Path(file.filename or 'overlay.png').name}"
    dest = config.UPLOAD_DIR / name
    dest.write_bytes(await file.read())
    return {"image_filename": name, "url": f"{config.PUBLIC_BASE_URL}/uploads/{name}"}


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    path = config.UPLOAD_DIR / Path(filename).name
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path)


@app.post("/api/overlays", response_model=OverlayEvent)
async def create_overlay(req: CreateOverlayRequest):
    if not store.get_channel(req.channel_id):
        raise HTTPException(404, "channel not found")
    if not (config.UPLOAD_DIR / Path(req.image_filename).name).exists():
        raise HTTPException(400, "image_filename not uploaded")
    overlay = OverlayEvent(
        id=new_id(), channel_id=req.channel_id, overlay_type=req.overlay_type,
        image_filename=req.image_filename,
        start_pdt=_aware(req.start_pdt), end_pdt=_aware(req.end_pdt),
        x_frac=req.x_frac, y_frac=req.y_frac, scale_frac=req.scale_frac)
    store.add_overlay(overlay)
    log.info("overlay created (absolute) id=%s ch=%s type=%s window=%s..%s",
             overlay.id, overlay.channel_id, overlay.overlay_type.value,
             overlay.start_pdt.isoformat(), overlay.end_pdt.isoformat())
    await _broadcast({"type": "overlay_created", "overlay": overlay.model_dump(mode="json")})
    return overlay


@app.post("/api/overlays/relative", response_model=OverlayEvent)
async def create_overlay_relative(req: CreateOverlayRelativeRequest):
    ch = store.get_channel(req.channel_id)
    if not ch or not ch.variants:
        raise HTTPException(404, "channel not found")
    if not (config.UPLOAD_DIR / Path(req.image_filename).name).exists():
        raise HTTPException(400, "image_filename not uploaded")
    text = await _fetch_text(ch.variants[0].origin_uri)
    pl = manifest.parse_media(text, ch.variants[0].origin_uri)
    edge = None
    has_pdt = bool(pl.segments) and _parse_pdt(pl.segments[-1].pdt) is not None
    if pl.segments:
        pdt = _parse_pdt(pl.segments[-1].pdt)
        if pdt:
            edge = pdt + timedelta(seconds=pl.segments[-1].duration)
    if edge is None:
        edge = datetime.now(timezone.utc)
    if not has_pdt:
        # Without PDT we cannot match segments to a wall-clock window; overlay
        # injection will never trigger. Surface this loudly.
        log.warning("origin variant 0 has NO EXT-X-PROGRAM-DATE-TIME — overlay "
                    "matching is PDT-based and will NOT work for this origin. "
                    "(channel=%s)", req.channel_id)
    start = edge + timedelta(seconds=req.start_in_seconds)
    end = start + timedelta(seconds=req.duration_seconds)
    overlay = OverlayEvent(
        id=new_id(), channel_id=req.channel_id, overlay_type=req.overlay_type,
        image_filename=req.image_filename, start_pdt=start, end_pdt=end,
        x_frac=req.x_frac, y_frac=req.y_frac, scale_frac=req.scale_frac)
    store.add_overlay(overlay)
    log.info("overlay created (relative) id=%s ch=%s type=%s edge=%s "
             "window=%s..%s (origin_has_pdt=%s)", overlay.id, overlay.channel_id,
             overlay.overlay_type.value, edge.isoformat(),
             overlay.start_pdt.isoformat(), overlay.end_pdt.isoformat(), has_pdt)
    await _broadcast({"type": "overlay_created", "overlay": overlay.model_dump(mode="json")})
    return overlay


@app.get("/api/channels/{channel_id}/overlays")
async def list_overlays(channel_id: str):
    return store.overlays_for_channel(channel_id)


@app.delete("/api/overlays/{overlay_id}")
async def delete_overlay(overlay_id: str):
    if not store.delete_overlay(overlay_id):
        raise HTTPException(404, "not found")
    await _broadcast({"type": "overlay_deleted", "overlay_id": overlay_id})
    return {"ok": True}


# --- manifest serving ------------------------------------------------------

@app.get("/hls/{channel_id}/master.m3u8")
async def serve_master(channel_id: str):
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    session = new_id()
    started = int(datetime.now(timezone.utc).timestamp() * 1000)
    out = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for v in ch.variants:
        w = v.width or ""
        h = v.height or ""
        url = (f"{config.PUBLIC_BASE_URL}/manifest/{channel_id}/{session}/{v.index}.m3u8"
               f"?h={h}&w={w}&codecs={v.codecs}&sessionStart={started}&tlSessionVer=2")
        out.append(v.inf_line)
        out.append(url)
    return PlainTextResponse("\n".join(out) + "\n",
                             media_type="application/vnd.apple.mpegurl")


@app.get("/manifest/{channel_id}/{session_id}/{variant_index}.m3u8")
async def serve_child(channel_id: str, session_id: str, variant_index: int):
    ch = store.get_channel(channel_id)
    if not ch or variant_index >= len(ch.variants):
        raise HTTPException(404, "variant not found")
    variant = ch.variants[variant_index]

    try:
        text = await _fetch_text(variant.origin_uri)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"origin fetch failed: {exc}")

    pl = manifest.parse_media(text, variant.origin_uri)
    origin_disc_seq = pl.discontinuity_sequence

    overlays = [o for o in store.overlays_for_channel(channel_id) if o.enabled]
    vp = variant_video_params(variant)

    def overlay_for(seg) -> Optional[OverlayEvent]:
        seg_pdt = _parse_pdt(seg.pdt)
        if seg_pdt is None:
            return None
        for o in overlays:
            if o.covers(seg_pdt):
                return o
        return None

    def make_job(seg, overlay: OverlayEvent) -> Job:
        return Job(
            channel_id=channel_id, variant_index=variant_index,
            overlay_id=overlay.id, seq=seg.seq, origin_url=seg.uri,
            overlay_image=str(config.UPLOAD_DIR / overlay.image_filename),
            vp=vp, overlay_type=overlay.overlay_type.value,
            x_frac=overlay.x_frac, y_frac=overlay.y_frac,
            scale_frac=overlay.scale_frac)

    # Look-ahead pass over the FULL origin window (including the buffered tail we
    # will hold back) so transcodes for soon-to-be-exposed segments start early.
    full_segments = pl.segments
    for seg in full_segments:
        ov = overlay_for(seg)
        if ov is not None:
            pool.ensure(make_job(seg, ov))

    # Hold the live edge back so overlay transcodes have time to complete.
    exposed = full_segments
    if not pl.endlist and config.BUFFER_SEGMENTS > 0 and \
            len(full_segments) > config.BUFFER_SEGMENTS:
        exposed = full_segments[:-config.BUFFER_SEGMENTS]

    window_min = exposed[0].seq if exposed else pl.media_sequence
    inject_flags: dict[int, bool] = {}
    prev_overlaid = False
    n_covered = n_ready = n_waiting = 0

    for seg in exposed:
        ov = overlay_for(seg)
        overlaid = False
        if ov is not None:
            n_covered += 1
            status = pool.ensure(make_job(seg, ov))
            if status == JobStatus.READY:
                seg.uri = (f"{config.PUBLIC_BASE_URL}/segment/{channel_id}/"
                           f"{variant_index}/{ov.id}/{seg.seq}.ts")
                overlaid = True
                n_ready += 1
            else:
                n_waiting += 1  # fall back to origin segment (no overlay yet)

        injected = (overlaid != prev_overlaid)
        inject_flags[seg.seq] = injected
        seg.discontinuity_before = seg.discontinuity_before or injected
        prev_overlaid = overlaid

    pl.segments = exposed
    pl.media_sequence = window_min
    scrolled = _tracker(channel_id, variant_index).observe(inject_flags, window_min)
    pl.discontinuity_sequence = origin_disc_seq + scrolled

    if overlays:
        log.info("child v%s: origin=%d exposed=%d overlays=%d covered=%d "
                 "overlaid=%d waiting=%d disc_seq=%d", variant_index,
                 len(full_segments), len(exposed), len(overlays), n_covered,
                 n_ready, n_waiting, pl.discontinuity_sequence)
        if n_covered == 0 and exposed:
            sp = _parse_pdt(exposed[0].pdt)
            ep = _parse_pdt(exposed[-1].pdt)
            log.info("  no segments matched any overlay window. exposed PDT "
                     "range=%s..%s; overlay windows=%s",
                     sp.isoformat() if sp else None,
                     ep.isoformat() if ep else None,
                     [(o.start_pdt.isoformat(), o.end_pdt.isoformat()) for o in overlays])

    return PlainTextResponse(manifest.render_media(pl),
                             media_type="application/vnd.apple.mpegurl")


@app.get("/segment/{channel_id}/{variant_index}/{overlay_id}/{seq}.ts")
async def serve_segment(channel_id: str, variant_index: int, overlay_id: str, seq: int):
    path = pool.segment_path(channel_id, variant_index, overlay_id, seq)
    if not path.exists():
        log.warning("segment requested but not on disk: ch=%s v%s overlay=%s seq=%s",
                    channel_id, variant_index, overlay_id, seq)
        raise HTTPException(404, "segment not ready")
    return FileResponse(path, media_type="video/mp2t")


@app.get("/api/channels/{channel_id}/debug")
async def debug_channel(channel_id: str, variant_index: int = 0):
    """Per-segment view of why overlays are/aren't being applied: each origin
    segment's PDT, which overlay (if any) covers it, and its transcode status
    plus any ffmpeg error. The single best place to diagnose 'no overlay'."""
    ch = store.get_channel(channel_id)
    if not ch or variant_index >= len(ch.variants):
        raise HTTPException(404, "channel/variant not found")
    variant = ch.variants[variant_index]
    text = await _fetch_text(variant.origin_uri)
    pl = manifest.parse_media(text, variant.origin_uri)
    overlays = [o for o in store.overlays_for_channel(channel_id) if o.enabled]

    rows = []
    for seg in pl.segments:
        seg_pdt = _parse_pdt(seg.pdt)
        covering = None
        for o in overlays:
            if seg_pdt is not None and o.covers(seg_pdt):
                covering = o
                break
        status = err = None
        if covering is not None:
            st = pool.status_of(channel_id, variant_index, covering.id, seg.seq)
            status = st.value if st else None
            err = pool.error_of(channel_id, variant_index, covering.id, seg.seq)
        rows.append({
            "seq": seg.seq, "pdt": seg.pdt, "pdt_parsed": bool(seg_pdt),
            "covered_by": covering.id if covering else None,
            "transcode_status": status,
            "error": (err[:400] if err else None),
        })

    return {
        "channel_id": channel_id,
        "variant_index": variant_index,
        "origin_uri": variant.origin_uri,
        "buffer_segments": config.BUFFER_SEGMENTS,
        "origin_segment_count": len(pl.segments),
        "any_segment_has_pdt": any(r["pdt_parsed"] for r in rows),
        "active_overlays": [
            {"id": o.id, "type": o.overlay_type.value,
             "start_pdt": o.start_pdt.isoformat(), "end_pdt": o.end_pdt.isoformat(),
             "image_exists": (config.UPLOAD_DIR / o.image_filename).exists()}
            for o in overlays],
        "segments": rows,
    }


@app.get("/api/health")
async def health():
    return {"ok": True, "channels": len(store.list_channels())}
