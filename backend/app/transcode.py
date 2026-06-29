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


def _video_encoder(vp: VideoParams) -> list[str]:
    """Codec-matched encoder args. HEVC is tagged hvc1 to match origin."""
    if vp.codec == "hevc":
        args = ["-c:v", "libx265", "-tag:v", "hvc1",
                "-preset", config.ENCODER_PRESET, "-crf", str(config.ENCODER_CRF),
                "-pix_fmt", vp.pix_fmt or "yuv420p"]
        # Keep each segment self-contained (IDR at start, no open-GOP).
        x265p = "no-open-gop=1:no-scenecut=1"
        args += ["-x265-params", x265p]
        return args
    args = ["-c:v", "libx264", "-preset", config.ENCODER_PRESET,
            "-crf", str(config.ENCODER_CRF), "-pix_fmt", vp.pix_fmt or "yuv420p",
            "-sc_threshold", "0"]
    if vp.profile:
        args += ["-profile:v", vp.profile]
    if vp.x264_level:
        args += ["-level", vp.x264_level]
    return args


def build_command(origin_url: str, overlay_image: str, vp: VideoParams,
                  overlay_type: str, offset: float, duration: float,
                  out_path: Path) -> list[str]:
    # Single filter_complex containing the squeeze video graph plus (optionally)
    # a matching audio-shift graph, so [outv]/[outa] are both defined.
    graphs = build_squeeze_filter(vp, overlay_type, offset, duration,
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
    cmd += ["-output_ts_offset", f"{offset:.4f}",
            "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", str(out_path)]
    return cmd


async def transcode_segment(origin_url: str, overlay_image: str, vp: VideoParams,
                            overlay_type: str, offset: float, duration: float,
                            out_path: Path) -> tuple[bool, str]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.ts")
    cmd = build_command(origin_url, overlay_image, vp, overlay_type,
                        offset, duration, tmp)
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
