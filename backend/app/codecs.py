"""Codec-string parsing and ffmpeg overlay-filter construction.

The whole point of transcoding *only* the overlay segments is that the result
must splice cleanly back into a stream we are otherwise passing through
untouched. That means the overlaid segment has to match the original variant's
codec, profile, level, pixel format, frame rate and resolution as closely as
possible. We derive those from the HLS master's `CODECS`, `RESOLUTION` and
`FRAME-RATE` attributes so no extra network probing is needed at ingest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# AVC profile_idc -> libx264 profile name.
_AVC_PROFILE = {
    66: "baseline",
    77: "main",
    88: "extended",
    100: "high",
    110: "high10",
    122: "high422",
    244: "high444",
}


@dataclass
class VideoParams:
    """Everything we need to reproduce a variant's video encode."""
    codec: str = "h264"          # ffmpeg encoder family
    profile: Optional[str] = None  # e.g. "high"
    level: Optional[float] = None  # e.g. 3.1
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    pix_fmt: str = "yuv420p"
    bitrate_kbps: Optional[int] = None

    @property
    def x264_level(self) -> Optional[str]:
        # libx264 -level takes "3.1" style strings.
        return None if self.level is None else f"{self.level:g}"


def parse_codecs_attr(codecs: str) -> VideoParams:
    """Parse an HLS CODECS attribute, e.g. ``avc1.64101f,mp4a.40.2``.

    For ``avc1`` the 6 hex digits after the dot are
    AVCProfileIndication(2) + profile_compatibility(2) + AVCLevelIndication(2),
    so ``64101f`` -> profile_idc=0x64=100 (High), level_idc=0x1f=31 -> 3.1.
    """
    params = VideoParams()
    for token in (codecs or "").split(","):
        token = token.strip().strip('"')
        if token.startswith("avc1") or token.startswith("avc3"):
            params.codec = "h264"
            parts = token.split(".")
            if len(parts) >= 2 and len(parts[1]) >= 6:
                hexcode = parts[1]
                try:
                    profile_idc = int(hexcode[0:2], 16)
                    level_idc = int(hexcode[4:6], 16)
                    params.profile = _AVC_PROFILE.get(profile_idc)
                    params.level = level_idc / 10.0
                except ValueError:
                    pass
        elif token.startswith("hvc1") or token.startswith("hev1"):
            params.codec = "hevc"
    return params


def video_params_from_variant(codecs: str, resolution: Optional[str],
                              frame_rate: Optional[str],
                              bandwidth: Optional[int]) -> VideoParams:
    p = parse_codecs_attr(codecs)
    if resolution and "x" in resolution:
        try:
            w, h = resolution.lower().split("x")
            p.width, p.height = int(w), int(h)
        except ValueError:
            pass
    if frame_rate:
        try:
            p.fps = float(frame_rate)
        except ValueError:
            pass
    if bandwidth:
        # Target ~90% of the advertised peak bandwidth for the video bitrate.
        p.bitrate_kbps = max(200, int(bandwidth * 0.9 / 1000))
    return p


# --- Overlay placement -----------------------------------------------------

# Overlay types map to a (scale_expr, x_expr, y_expr) recipe evaluated against
# the variant's pixel dimensions. W/H are numeric main dimensions; overlay_w /
# overlay_h are the scaled overlay's own dimensions (resolved by ffmpeg).
OVERLAY_TYPES = {"lband", "lower_third", "top_banner", "full_frame", "custom"}


def build_overlay_filter(vp: VideoParams, overlay_type: str,
                         x_frac: float = 0.0, y_frac: float = 0.0,
                         scale_frac: float = 1.0) -> str:
    """Return an ffmpeg ``-filter_complex`` graph that burns input #1 (the
    overlay image) onto input #0 (the video), scaled for this variant.

    Input pads: ``[0:v]`` video, ``[1:v]`` overlay image. Output pad: ``[v]``.
    """
    W = vp.width or 1280
    H = vp.height or 720

    if overlay_type == "full_frame":
        scale = f"scale={W}:{H}"
        x, y = "0", "0"
    elif overlay_type == "top_banner":
        scale = f"scale={W}:-2"
        x, y = "0", "0"
    elif overlay_type == "lower_third":
        scale = f"scale={W}:-2"
        x, y = "0", f"{int(H * 2 / 3)}"
    elif overlay_type == "custom":
        sw = max(2, int(W * scale_frac))
        scale = f"scale={sw}:-2"
        # x_frac/y_frac in [0,1] position the overlay within the free space.
        x = f"(main_w-overlay_w)*{x_frac:.4f}"
        y = f"(main_h-overlay_h)*{y_frac:.4f}"
    else:  # "lband" default: full-width band pinned to the bottom edge.
        scale = f"scale={W}:-2"
        x, y = "0", "main_h-overlay_h"

    return f"[1:v]{scale}[ov];[0:v][ov]overlay={x}:{y}[v]"
