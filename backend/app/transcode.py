"""ffmpeg squeeze-back overlay transcode for a single segment.

Only the segments inside an overlay window pass through here; everything else is
served straight from the origin. The main video is squeezed into the overlay
"pocket" (animated by an eased factor) and the overlay/ad art is composited on
top. The encode matches the origin codec family (HEVC->libx265, H.264->libx264)
so the overlaid segment splices cleanly after an ``#EXT-X-DISCONTINUITY``.

Timestamps: each segment's PTS is reset to 0 and shifted by ``offset`` (its
position within the overlay event) for both video and audio, so segments inside
one event stay continuous while the easing ``t`` is event-global. Audio is
re-encoded (cheap AAC) to keep it aligned with the shifted video.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from . import config
from .codecs import VideoParams, build_squeeze_filter
from .models import VariantInfo

log = logging.getLogger("overlay.transcode")


def variant_video_params(v: VariantInfo) -> VideoParams:
    return VideoParams(
        codec=(v.codecs.split(",")[0].strip().lower().startswith(("hvc1", "hev1"))
               and "hevc" or "h264"),
        profile=v.profile,
        level=v.level,
        width=v.width,
        height=v.height,
        fps=v.fps,
        pix_fmt=v.pix_fmt or "yuv420p",
        bitrate_kbps=v.bitrate_kbps,
        has_audio=v.has_audio,
    )


def _hw_video_encoder(vp: VideoParams) -> Optional[list[str]]:
    """Optional hardware encoder args (NVIDIA NVENC / Intel QSV), selected by
    OVERLAY_HWACCEL. Returns None for the default software path. These accept the
    CPU-filtered frames directly (no hwupload needed) and are tagged hvc1 for
    HEVC so the segment still splices cleanly. Low-latency presets keep each
    short segment self-contained (single IDR, no B-frames/lookahead)."""
    hw = config.HWACCEL
    q = str(config.ENCODER_CRF)
    pix = vp.pix_fmt or "yuv420p"
    if hw == "nvenc":
        if vp.codec == "hevc":
            return ["-c:v", "hevc_nvenc", "-tag:v", "hvc1", "-preset", "p1",
                    "-tune", "ll", "-rc", "vbr", "-cq", q, "-bf", "0",
                    "-g", "300", "-pix_fmt", pix]
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll",
                "-rc", "vbr", "-cq", q, "-bf", "0", "-g", "300", "-pix_fmt", pix]
    if hw == "qsv":
        if vp.codec == "hevc":
            return ["-c:v", "hevc_qsv", "-tag:v", "hvc1", "-preset", "veryfast",
                    "-global_quality", q, "-bf", "0", "-look_ahead", "0"]
        return ["-c:v", "h264_qsv", "-preset", "veryfast",
                "-global_quality", q, "-bf", "0", "-look_ahead", "0"]
    return None


def _video_encoder(vp: VideoParams) -> list[str]:
    """Codec-matched encoder args, tuned for fast single-segment encodes. HEVC is
    tagged hvc1 to match origin. Each segment is a self-contained closed-GOP with
    an IDR at the start, no B-frames and no lookahead, so it encodes quickly and
    splices cleanly after an ``#EXT-X-DISCONTINUITY``."""
    hw = _hw_video_encoder(vp)
    if hw is not None:
        return hw
    threads = str(config.ENCODER_THREADS)
    if vp.codec == "hevc":
        args = ["-c:v", "libx265", "-tag:v", "hvc1",
                "-preset", config.ENCODER_PRESET, "-crf", str(config.ENCODER_CRF),
                "-pix_fmt", vp.pix_fmt or "yuv420p"]
        # Fast, self-contained segments: closed GOP, no scenecut/open-GOP, no
        # B-frames, no lookahead; quiet the per-run x265 banner.
        x265p = ("no-open-gop=1:no-scenecut=1:bframes=0:rc-lookahead=0:"
                 "b-adapt=0:no-info=1:log-level=none")
        if config.ENCODER_THREADS:
            x265p += f":frame-threads={config.ENCODER_THREADS}"
        args += ["-x265-params", x265p]
        return args
    args = ["-c:v", "libx264", "-preset", config.ENCODER_PRESET,
            "-tune", "zerolatency",
            "-crf", str(config.ENCODER_CRF), "-pix_fmt", vp.pix_fmt or "yuv420p",
            "-sc_threshold", "0", "-bf", "0",
            "-x264-params", "rc-lookahead=0:sync-lookahead=0:sliced-threads=0",
            "-threads", threads]
    if vp.profile:
        args += ["-profile:v", vp.profile]
    if vp.x264_level:
        args += ["-level", vp.x264_level]
    return args


def build_command(origin_url: str, overlay_image: str, vp: VideoParams,
                  overlay_type: str, event_offset: float, duration: float,
                  seg_duration: float, mux_offset: float,
                  out_path: Path) -> list[str]:
    # Single filter_complex containing the squeeze video graph plus (optionally)
    # a matching audio-shift graph, so [outv]/[outa] are both defined.
    # event_offset drives the easing/art fade (may be negative); mux_offset (>=0)
    # keeps the segments of one event continuous in output PTS.
    graphs = build_squeeze_filter(vp, overlay_type, event_offset, duration,
                                  seg_duration,
                                  t_in=config.SQUEEZE_IN, t_out=config.SQUEEZE_OUT)
    maps = ["-map", "[outv]"]
    if vp.has_audio:
        # Audio reset to 0-based to stay aligned with the 0-based video graph;
        # both get the same -output_ts_offset below for cross-segment continuity.
        graphs += ";[0:a]asetpts=PTS-STARTPTS,aresample=async=1[outa]"
        maps += ["-map", "[outa]"]
    cmd = [
        config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-rw_timeout", "15000000",
        "-i", origin_url,
        "-loop", "1", "-i", overlay_image,   # still image, looped over the segment
        "-filter_complex", graphs,
        *maps,
        "-force_key_frames", "expr:eq(n,0)",  # IDR at segment start
        *_video_encoder(vp),
    ]
    if vp.fps:
        cmd += ["-r", f"{vp.fps:g}"]
    if vp.has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    # Shift output timestamps so segments inside one event are continuous.
    cmd += ["-output_ts_offset", f"{mux_offset:.4f}",
            "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", str(out_path)]
    return cmd


async def transcode_segment(origin_url: str, overlay_image: str, vp: VideoParams,
                            overlay_type: str, event_offset: float, duration: float,
                            seg_duration: float, mux_offset: float,
                            out_path: Path) -> tuple[bool, str]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.ts")
    cmd = build_command(origin_url, overlay_image, vp, overlay_type,
                        event_offset, duration, seg_duration, mux_offset, tmp)
    log.debug("ffmpeg cmd: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        err = stderr.decode("utf-8", "replace")
        log.warning("ffmpeg FAILED rc=%s origin=%s\n  cmd: %s\n  stderr: %s",
                    proc.returncode, origin_url, " ".join(cmd), err[-1500:])
        return False, err[-2000:]
    tmp.replace(out_path)
    return True, ""
