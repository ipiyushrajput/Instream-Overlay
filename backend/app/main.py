"""FastAPI application: manifest mirror + overlay injection + operator API."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from . import config, db, defaults, manifest
from .codecs import video_params_from_variant
from .models import (Channel, CreateOverlayRelativeRequest, CreateOverlayRequest,
                     IngestRequest, OverlayEvent, UpdateChannelRequest, VariantInfo)
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
    for key in [k for k in _rendition_segs if k[0] == channel_id]:
        _rendition_segs.pop(key, None)
    _synth_pdt.pop(channel_id, None)


# Per-channel synthesized PROGRAM-DATE-TIME (seq -> ISO) for origins that ship
# no PDT at all. Assigned once per segment from the wall clock and reused so the
# value is stable across reloads (required for playlist immutability).
_synth_pdt: dict[str, dict[int, str]] = {}

# Remembered rendition segment URLs/tags by seq (keyed by (channel, idx)) so we
# can serve the same buffer-held window the video timeline exposes even after
# the origin rendition playlist scrolls those segments off.
_rendition_segs: dict[tuple, dict[int, tuple]] = {}


def _ensure_pdt(channel_id: str, pl) -> bool:
    """Ensure every segment has a PDT. If the origin already provides one
    (top-of-playlist or per-segment), the parser's forward-fill has it covered
    and we do nothing. If there is none at all, synthesize from UTC wall clock,
    anchored once per segment so it stays stable. Returns True if PDT is present
    (native or synthesized)."""
    if any(s.pdt for s in pl.segments):
        return True
    if not pl.segments:
        return False
    synth = _synth_pdt.setdefault(channel_id, {})
    running = None
    for seg in pl.segments:
        if seg.seq in synth:
            seg.pdt = synth[seg.seq]
            running = _parse_pdt(seg.pdt)
        else:
            if running is None:
                running = datetime.now(timezone.utc)
            seg.pdt = running.isoformat()
            synth[seg.seq] = seg.pdt
        running = running + timedelta(seconds=seg.duration or 0.0)
    # Prune entries below the current window.
    window_min = pl.segments[0].seq
    for s in [s for s in synth if s < window_min - 50]:
        synth.pop(s, None)
    return True


async def _channel_media(channel_id: str, origin_uri: str):
    """Fetch + parse a channel's origin media playlist, with PDT ensured."""
    text = await _fetch_origin_cached(app.state.http, origin_uri)
    pl = manifest.parse_media(text, origin_uri)
    _ensure_pdt(channel_id, pl)
    return pl


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


def _is_hevc(ch: Channel) -> bool:
    return any((v.codecs or "").lower().startswith(("hvc1", "hev1")) for v in ch.variants)


def _effective_buffer(ch: Channel) -> int:
    """Segments to hold behind the live edge. HEVC software encoding is much
    heavier than H.264, so we hold back more to give the transcoder headroom and
    avoid the buffering/freezing seen during HEVC overlay transitions."""
    base = config.BUFFER_SEGMENTS
    return base + config.HEVC_EXTRA_BUFFER if _is_hevc(ch) else base


def _min_lead_seconds(target_duration: int, ch: Optional[Channel] = None) -> int:
    """Minimum lead before an overlay window starts. Must cover the buffer
    hold-back (where transcoding happens) plus margin. The squeeze + codec-match
    (HEVC) encode is heavier, so the effective buffer is larger for HEVC."""
    td = target_duration or 6
    buf = _effective_buffer(ch) if ch is not None else config.BUFFER_SEGMENTS
    return int((buf + 1) * td)


log = logging.getLogger("overlay.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    config.setup_logging()
    log.info("starting up: buffer=%d segs, workers=%d, verify_tls=%s, data=%s",
             config.BUFFER_SEGMENTS, config.MAX_TRANSCODE_WORKERS,
             config.VERIFY_TLS, config.DATA_DIR)
    db.init()
    store.load_from_db()
    defaults.ensure_default_overlays()
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

def _probe_master(text: str, master_url: str) -> tuple[list[VariantInfo], list[str], list[dict]]:
    """Parse a master playlist into VariantInfos + preserved header lines +
    audio/subtitle renditions."""
    if not manifest.is_master(text):
        return ([VariantInfo(index=0, origin_uri=master_url,
                             inf_line="#EXT-X-STREAM-INF:BANDWIDTH=2000000")], [], [])
    master = manifest.parse_master(text, master_url)
    if not master.variants:
        raise HTTPException(400, "No variants found in master playlist")
    variants = []
    for idx, mv in enumerate(master.variants):
        vp = video_params_from_variant(mv.codecs, mv.resolution,
                                       mv.frame_rate, mv.bandwidth)
        variants.append(VariantInfo(
            index=idx, origin_uri=mv.uri, inf_line=mv.inf_line,
            codecs=mv.codecs, resolution=mv.resolution,
            frame_rate=mv.frame_rate, bandwidth=mv.bandwidth,
            width=vp.width, height=vp.height, fps=vp.fps,
            profile=vp.profile, level=vp.level, pix_fmt=vp.pix_fmt,
            bitrate_kbps=vp.bitrate_kbps, has_audio=vp.has_audio))
    renditions = [{"idx": r.idx, "line": r.line, "origin_uri": r.origin_uri,
                   "name": r.name} for r in master.renditions]
    return (variants, master.other_lines, renditions)


@app.post("/api/ingest", response_model=Channel)
async def ingest(req: IngestRequest):
    try:
        text = await _fetch_text(req.master_url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Failed to fetch master: {exc}")

    variants, master_other_lines, renditions = _probe_master(text, req.master_url)
    channel = Channel(id=new_id(), name=req.name or "channel",
                      master_url=req.master_url, variants=variants,
                      master_other_lines=master_other_lines, renditions=renditions)
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


@app.put("/api/channels/{channel_id}", response_model=Channel)
async def update_channel(channel_id: str, req: UpdateChannelRequest):
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    if req.name is not None:
        ch.name = req.name
    if req.master_url is not None and req.master_url != ch.master_url:
        # Re-probe the variants from the new origin and reset timelines.
        try:
            text = await _fetch_text(req.master_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Failed to fetch master: {exc}")
        variants, other, renditions = _probe_master(text, req.master_url)
        ch.master_url = req.master_url
        ch.variants = variants
        ch.master_other_lines = other
        ch.renditions = renditions
        _drop_timelines(channel_id)
    store.update_channel(ch)
    log.info("updated channel=%s name=%s", channel_id, ch.name)
    return ch


@app.get("/api/channels/{channel_id}/status")
async def channel_status(channel_id: str):
    """Live-edge + buffer info for the operator console."""
    ch = store.get_channel(channel_id)
    if not ch or not ch.variants:
        raise HTTPException(404, "channel not found")
    pl = await _channel_media(channel_id, ch.variants[0].origin_uri)
    edge = _live_edge_pdt(pl)
    overlays = store.overlays_for_channel(channel_id)
    return {
        "channel_id": channel_id,
        "name": ch.name,
        "status": ch.status,
        "codec": "hevc" if _is_hevc(ch) else "h264",
        "live_edge_pdt": edge.isoformat() if edge else None,
        "origin_has_pdt": edge is not None,
        "buffer_segments": _effective_buffer(ch),
        "target_duration": pl.target_duration,
        "min_lead_seconds": _min_lead_seconds(pl.target_duration, ch),
        "segment_count": len(pl.segments),
        "overlay_count": len(overlays),
        "active_overlays": sum(1 for o in overlays
                               if _overlay_status(o, edge) == "active"),
    }


# --- overlays --------------------------------------------------------------

@app.get("/api/defaults")
async def list_default_overlays():
    """Built-in overlay band presets (item 7) — no upload needed."""
    return defaults.list_defaults()


@app.post("/api/overlays/upload")
async def upload_overlay(file: UploadFile = File(...)):
    config.ensure_dirs()
    name = f"{new_id()}_{Path(file.filename or 'overlay.png').name}"
    dest = config.UPLOAD_DIR / name
    dest.write_bytes(await file.read())
    return {"image_filename": name, "url": f"{config.PUBLIC_BASE_URL}/uploads/{name}"}


@app.post("/api/overlays/from-url")
async def overlay_from_url(payload: dict):
    """Import an overlay image from a URL the user provides (item 7)."""
    url = (payload or {}).get("url", "").strip()
    if not url:
        raise HTTPException(400, "url required")
    try:
        resp = await app.state.http.get(url)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"failed to fetch image: {exc}")
    config.ensure_dirs()
    ext = Path(url.split("?")[0]).suffix or ".png"
    name = f"{new_id()}{ext}"
    (config.UPLOAD_DIR / name).write_bytes(resp.content)
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
    pl = await _channel_media(req.channel_id, ch.variants[0].origin_uri)
    edge = _live_edge_pdt(pl)
    has_pdt = edge is not None
    if edge is None:
        edge = datetime.now(timezone.utc)
    # Enforce a minimum lead so the segments are transcoded before they reach the
    # buffer-held live edge (larger for HEVC). Clamp up rather than reject.
    min_lead = _min_lead_seconds(pl.target_duration, ch)
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
            edge = _live_edge_pdt(await _channel_media(channel_id, ch.variants[0].origin_uri))
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


def _teardown_processing(ch: Channel) -> None:
    """Drop the live processing state (timelines + cached manifests) for a
    channel without removing the channel itself."""
    for v in ch.variants:
        _origin_cache.pop(v.origin_uri, None)
        _origin_locks.pop(v.origin_uri, None)
    _drop_timelines(ch.id)


@app.post("/api/channels/{channel_id}/stop", response_model=Channel)
async def stop_channel(channel_id: str):
    """Stop ingestion but KEEP the channel (name + origin) in the DB so it stays
    in the channel list as 'stopped' and can be started again later."""
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    _teardown_processing(ch)
    ch.status = "stopped"
    store.update_channel(ch)
    log.info("stopped channel=%s (kept)", channel_id)
    await _broadcast({"type": "channel_stopped", "channel_id": channel_id})
    return ch


@app.post("/api/channels/{channel_id}/start", response_model=Channel)
async def start_channel(channel_id: str):
    """Resume ingestion for a previously stopped channel."""
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    ch.status = "active"
    store.update_channel(ch)
    log.info("started channel=%s", channel_id)
    await _broadcast({"type": "channel_started", "channel_id": channel_id})
    return ch


@app.delete("/api/channels/{channel_id}")
async def delete_channel_ep(channel_id: str):
    """Permanently delete a channel and its overlays from the DB."""
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    _teardown_processing(ch)
    store.delete_channel(channel_id)
    log.info("deleted channel=%s", channel_id)
    await _broadcast({"type": "channel_deleted", "channel_id": channel_id})
    return {"ok": True}


# --- rendition (audio / subtitle) mirroring --------------------------------

def _reconstruct_seg_url(template: str, seq: int) -> Optional[str]:
    """Build a rendition segment URL for ``seq`` from a sibling URL template by
    replacing its trailing numeric run (e.g. seg_0115651.vtt -> seg_0115660.vtt),
    preserving zero-padding. Used when a needed seq is no longer in the origin
    playlist window."""
    m = re.search(r"(\d+)(\.\w+)$", template)
    if not m:
        return None
    width = len(m.group(1))
    return template[:m.start(1)] + str(seq).zfill(width) + m.group(2)


@app.get("/rendition/{channel_id}/{rname}.m3u8")
async def serve_rendition(channel_id: str, rname: str):
    """Mirror an audio/subtitle rendition, kept in lock-step with the video.

    Renditions must stay aligned with the video variants (same MEDIA-SEQUENCE,
    DISCONTINUITY-SEQUENCE, discontinuity positions, PDT and buffer hold-back) or
    the player buffers/desyncs. We therefore render the rendition through the
    *video* timeline's frozen structure, substituting the origin rendition's own
    (absolute) segment URLs — no transcoding, no overlay."""
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    rend = next((r for r in ch.renditions if r.get("name") == rname), None)
    if not rend:
        raise HTTPException(404, "rendition not found")
    idx = rend["idx"]
    try:
        text = await _fetch_origin_cached(app.state.http, rend["origin_uri"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"rendition fetch failed: {exc}")
    sub_pl = manifest.parse_media(text, rend["origin_uri"])  # absolute seg URIs

    # Remember every seq we see so we can serve older buffer-held segments too.
    mem = _rendition_segs.setdefault((channel_id, idx), {})
    template = sub_pl.segments[0].uri if sub_pl.segments else None
    for s in sub_pl.segments:
        mem[s.seq] = (s.uri, s.tags)

    # Mirror the video variant-0 timeline so both stay perfectly aligned.
    tl = _timeline(channel_id, 0)
    if not tl.frozen:
        # Video hasn't been requested yet — serve the origin rendition as-is.
        for s in [s for s in mem if s < (sub_pl.media_sequence - 200)]:
            mem.pop(s, None)
        return PlainTextResponse(manifest.render_media(sub_pl),
                                 media_type="application/vnd.apple.mpegurl")

    def uri_for(seq: int) -> Optional[str]:
        e = mem.get(seq)
        if e:
            return e[0]
        return _reconstruct_seg_url(template, seq) if template else None

    def tags_for(seq: int):
        e = mem.get(seq)
        return e[1] if e else []

    out = tl.render(sub_pl.discontinuity_sequence, sub_pl.target_duration,
                    version=sub_pl.version, header_extra=sub_pl.header_extra,
                    uri_for=uri_for, tags_for=tags_for)
    # Prune remembered entries well below the rendered window.
    for s in [s for s in mem if s < out.media_sequence - 200]:
        mem.pop(s, None)
    return PlainTextResponse(manifest.render_media(out),
                             media_type="application/vnd.apple.mpegurl")


# --- manifest serving ------------------------------------------------------

@app.get("/hls/{channel_id}/master.m3u8")
async def serve_master(channel_id: str):
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    session = new_id()
    started = int(datetime.now(timezone.utc).timestamp() * 1000)
    # Preserve the origin master's header lines (EXT-X-INDEPENDENT-SEGMENTS,
    # EXT-X-MEDIA audio/subtitle renditions, …); fall back to a minimal header.
    out = list(ch.master_other_lines) if ch.master_other_lines else \
        ["#EXTM3U", "#EXT-X-VERSION:3"]
    if not out or out[0] != "#EXTM3U":
        out.insert(0, "#EXTM3U")
    # Audio/subtitle renditions: point their URI at our /rendition endpoint so
    # the rendition playlist is also served from our server (segments absolute).
    for r in ch.renditions:
        rname = r.get("name") or f"rendition-{r['idx']}"
        rurl = f"{config.PUBLIC_BASE_URL}/rendition/{channel_id}/{rname}.m3u8"
        out.append(manifest.rewrite_media_uri(r["line"], rurl))
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
        pl = await _channel_media(channel_id, variant.origin_uri)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"origin fetch failed: {exc}")

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
        seg_pdt = _parse_pdt(seg.pdt)
        offset = 0.0
        if seg_pdt is not None:
            offset = max(0.0, (seg_pdt - overlay.start_pdt).total_seconds())
        duration = (overlay.end_pdt - overlay.start_pdt).total_seconds()
        return Job(
            channel_id=channel_id, variant_index=variant_index,
            overlay_id=overlay.id, seq=seg.seq, origin_url=seg.uri,
            overlay_image=str(config.UPLOAD_DIR / overlay.image_filename),
            vp=vp, overlay_type=overlay.overlay_type.value,
            offset=offset, duration=duration)

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
    # Mirror the origin's window length so the output isn't shorter than origin.
    window_size = len(pl.segments) or 1
    tl.advance(pl.segments, _effective_buffer(ch), decide, window_size)
    out = tl.render(pl.discontinuity_sequence, pl.target_duration,
                    version=pl.version, header_extra=pl.header_extra)

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
