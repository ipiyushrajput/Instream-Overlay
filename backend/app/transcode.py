"""ffmpeg overlay burn-in for a single segment.

Only the segments inside an overlay window pass through here; everything else is
served straight from the origin. The encode is matched to the variant
(profile/level/pix_fmt/fps/bitrate) and forced to start on an IDR frame so the
overlaid segment splices cleanly after an `#EXT-X-DISCONTINUITY`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import config
from .codecs import VideoParams, build_overlay_filter
from .models import VariantInfo


def variant_video_params(v: VariantInfo) -> VideoParams:
    return VideoParams(
        codec="h264",
        profile=v.profile,
        level=v.level,
        width=v.width,
        height=v.height,
        fps=v.fps,
        pix_fmt=v.pix_fmt or "yuv420p",
        bitrate_kbps=v.bitrate_kbps,
    )


def build_command(origin_url: str, overlay_image: str, vp: VideoParams,
                  overlay_type: str, x_frac: float, y_frac: float,
                  scale_frac: float, out_path: Path) -> list[str]:
    filt = build_overlay_filter(vp, overlay_type, x_frac, y_frac, scale_frac)
    cmd = [
        config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-copyts",
        "-i", origin_url,
        "-i", overlay_image,
        "-filter_complex", filt,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", vp.pix_fmt or "yuv420p",
        # Force the first frame to be an IDR so the segment is self-contained.
        "-force_key_frames", "expr:gte(t,0)",
        "-sc_threshold", "0",
    ]
    if vp.profile:
        cmd += ["-profile:v", vp.profile]
    if vp.x264_level:
        cmd += ["-level", vp.x264_level]
    if vp.fps:
        cmd += ["-r", f"{vp.fps:g}"]
    if vp.bitrate_kbps:
        cmd += ["-b:v", f"{vp.bitrate_kbps}k",
                "-maxrate", f"{vp.bitrate_kbps}k",
                "-bufsize", f"{vp.bitrate_kbps * 2}k"]
    # Copy audio untouched -> no re-encode drift, A/V stays in sync.
    cmd += ["-c:a", "copy",
            "-muxdelay", "0", "-muxpreload", "0",
            "-f", "mpegts", str(out_path)]
    return cmd


async def transcode_segment(origin_url: str, overlay_image: str, vp: VideoParams,
                            overlay_type: str, x_frac: float, y_frac: float,
                            scale_frac: float, out_path: Path) -> tuple[bool, str]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.ts")
    cmd = build_command(origin_url, overlay_image, vp, overlay_type,
                        x_frac, y_frac, scale_frac, tmp)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False, stderr.decode("utf-8", "replace")[-2000:]
    tmp.replace(out_path)
    return True, ""
