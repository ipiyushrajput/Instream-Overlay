"""FastAPI application: manifest mirror + overlay injection + operator API."""
from __future__ import annotations

import asyncio
import logging
import time
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
from .timeline import VariantTimeline
from .transcode import variant_video_params
from .worker import Job, JobStatus, pool

# Per-(channel, variant) frozen-decision timelines.
_timelines: dict[tuple, VariantTimeline] = {}


def _timeline(channel_id: str, variant_index: int) -> VariantTimeline:
    key = (channel_id, variant_index)
    if key not in _timelines:
        _timelines[key] = VariantTimeline()
    return _timelines[key]


def _drop_timelines(channel_id: str) -> None:
    for key in [k for k in _timelines if k[0] == channel_id]:
        _timelines.pop(key, None)


# Short-TTL cache of origin manifests, shared across player + status requests so
# we don't hammer the origin (one fetch per variant per ~1.5s instead of one per
# caller). Keyed by origin child URL.
_origin_cache: dict[str, tuple[float, str]] = {}
_origin_locks: dict[str, asyncio.Lock] = {}
ORIGIN_CACHE_TTL = 1.5


async def _fetch_origin_cached(http: httpx.AsyncClient, url: str) -> str:
    now = time.monotonic()
    hit = _origin_cache.get(url)
    if hit and now - hit[0] < ORIGIN_CACHE_TTL:
        return hit[1]
    lock = _origin_locks.setdefault(url, asyncio.Lock())
    async with lock:
        hit = _origin_cache.get(url)
        if hit and time.monotonic() - hit[0] < ORIGIN_CACHE_TTL:
            return hit[1]
        resp = await http.get(url)
        resp.raise_for_status()
        _origin_cache[url] = (time.monotonic(), resp.text)
        return resp.text


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


def _live_edge_pdt(pl: manifest.MediaPlaylist) -> Optional[datetime]:
    """Wall-clock at the live edge = last segment's PDT + its duration."""
    if not pl.segments:
        return None
    pdt = _parse_pdt(pl.segments[-1].pdt)
    if pdt is None:
        return None
    return pdt + timedelta(seconds=pl.segments[-1].duration or 0.0)


def _overlay_status(overlay: OverlayEvent, edge: Optional[datetime]) -> str:
    """scheduled -> active -> completed, derived from the live edge."""
    injected = store.injected_count(overlay.id)
    if edge is None:
        return "scheduled"
    if edge < overlay.start_pdt:
        return "scheduled"
    if edge < overlay.end_pdt:
        return "active"
    # Window has fully passed the live edge.
    return "completed" if injected else "expired"


def _min_lead_seconds(target_duration: int) -> int:
    """Minimum lead before an overlay window starts. The transcode headroom is
    already the buffer depth (segments are transcoded while held back), so the
    lead only needs to push the window just past the current live edge. Two
    segment durations gives comfortable margin (~10-12s)."""
    td = target_duration or 6
    return int(2 * td)


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
    text = await _fetch_origin_cached(app.state.http, ch.variants[0].origin_uri)
    pl = manifest.parse_media(text, ch.variants[0].origin_uri)
    edge = _live_edge_pdt(pl)
    overlays = store.overlays_for_channel(channel_id)
    return {
        "channel_id": channel_id,
        "name": ch.name,
        "live_edge_pdt": edge.isoformat() if edge else None,
        "origin_has_pdt": edge is not None,
        "buffer_segments": config.BUFFER_SEGMENTS,
        "target_duration": pl.target_duration,
        "min_lead_seconds": _min_lead_seconds(pl.target_duration),
        "segment_count": len(pl.segments),
        "overlay_count": len(overlays),
        "active_overlays": sum(1 for o in overlays
                               if _overlay_status(o, edge) == "active"),
    }


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
    text = await _fetch_origin_cached(app.state.http, ch.variants[0].origin_uri)
    pl = manifest.parse_media(text, ch.variants[0].origin_uri)
    edge = _live_edge_pdt(pl)
    has_pdt = edge is not None
    if edge is None:
        edge = datetime.now(timezone.utc)
        log.warning("origin variant 0 has NO EXT-X-PROGRAM-DATE-TIME — overlay "
                    "matching is PDT-based and will NOT work for this origin. "
                    "(channel=%s)", req.channel_id)
    # Enforce a minimum lead so the segments are transcoded before they reach the
    # buffer-held live edge (item 7). Clamp up rather than reject.
    min_lead = _min_lead_seconds(pl.target_duration)
    start_in = max(float(req.start_in_seconds), float(min_lead))
    start = edge + timedelta(seconds=start_in)
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
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    edge = None
    if ch.variants:
        try:
            text = await _fetch_origin_cached(app.state.http, ch.variants[0].origin_uri)
            edge = _live_edge_pdt(manifest.parse_media(text, ch.variants[0].origin_uri))
        except Exception:  # noqa: BLE001
            edge = None
    out = []
    for o in store.overlays_for_channel(channel_id):
        d = o.model_dump(mode="json")
        d["status"] = _overlay_status(o, edge)
        d["injected_count"] = store.injected_count(o.id)
        out.append(d)
    out.sort(key=lambda d: d["start_pdt"])
    return out


@app.delete("/api/overlays/{overlay_id}")
async def delete_overlay(overlay_id: str):
    if not store.delete_overlay(overlay_id):
        raise HTTPException(404, "not found")
    await _broadcast({"type": "overlay_deleted", "overlay_id": overlay_id})
    return {"ok": True}


@app.delete("/api/channels/{channel_id}")
async def stop_channel(channel_id: str):
    """Stop ingestion for a channel: removes it and its overlays, drops the
    frozen timelines and cached origin manifests. The frontend stops polling
    once this returns. Transcoded files on disk are left for cache reuse."""
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    for v in ch.variants:
        _origin_cache.pop(v.origin_uri, None)
        _origin_locks.pop(v.origin_uri, None)
    _drop_timelines(channel_id)
    store.delete_channel(channel_id)
    log.info("stopped channel=%s", channel_id)
    await _broadcast({"type": "channel_stopped", "channel_id": channel_id})
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
        text = await _fetch_origin_cached(app.state.http, variant.origin_uri)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"origin fetch failed: {exc}")

    pl = manifest.parse_media(text, variant.origin_uri)
    overlays = [o for o in store.overlays_for_channel(channel_id) if o.enabled]
    vp = variant_video_params(variant)
    tl = _timeline(channel_id, variant_index)

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

    # Look-ahead: kick off transcodes for EVERY covered segment in the full
    # origin window (including the buffered tail) so they're ready before the
    # frozen edge reaches them.
    for seg in pl.segments:
        ov = overlay_for(seg)
        if ov is not None and seg.seq > tl.max_frozen_seq:
            pool.ensure(make_job(seg, ov))

    def decide(seg):
        """origin / overlay(READY) / None(wait) — see timeline.DecideFn."""
        ov = overlay_for(seg)
        if ov is None:
            return ("origin", seg.uri, None)
        status = pool.ensure(make_job(seg, ov))
        if status == JobStatus.READY:
            store.mark_injected(ov.id, seg.seq)
            rel = f"/segment/{channel_id}/{variant_index}/{ov.id}/{seg.seq}.ts"
            return ("overlay", rel, ov.id)
        if status == JobStatus.FAILED:
            return ("origin", seg.uri, None)  # don't wait on a failed transcode
        return None  # pending -> hold the edge here until it's ready

    before_max = tl.max_frozen_seq
    tl.advance(pl.segments, config.BUFFER_SEGMENTS, decide)
    out = tl.render(pl.discontinuity_sequence, pl.target_duration,
                    version=pl.version, header_tags=pl.header_tags)

    if overlays and tl.max_frozen_seq != before_max:
        n_overlay = sum(1 for d in tl.frozen.values() if d.kind == "overlay")
        log.info("child v%s: origin=%d exposed=%d overlays=%d overlaid=%d "
                 "disc_seq=%d edge_seq=%d", variant_index, len(pl.segments),
                 len(out.segments), len(overlays), n_overlay,
                 out.discontinuity_sequence, tl.max_frozen_seq)

    return PlainTextResponse(manifest.render_media(out),
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
