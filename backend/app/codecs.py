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
    codec: str = "h264"          # 'h264' | 'hevc'
    profile: Optional[str] = None  # e.g. "high"
    level: Optional[float] = None  # e.g. 3.1
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    pix_fmt: str = "yuv420p"
    bitrate_kbps: Optional[int] = None
    has_audio: bool = True        # audio stream present in the segments

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
    params = VideoParams(has_audio=False)
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
            # hvcC form: hvc1.<profile>.<compat>.L<level>.<constraints>
            parts = token.split(".")
            for p in parts:
                if p.startswith("L") and p[1:].isdigit():
                    params.level = int(p[1:]) / 30.0  # HEVC level = general_level_idc/30
        elif token.startswith(("mp4a", "ac-3", "ec-3", "opus", "aac")):
            params.has_audio = True
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

# The four squeeze-back overlay types. Each defines the "pocket" the main video
# is squeezed into, as fractions of the full frame: (frac_w, frac_h, frac_x,
# frac_y), derived from the user's 1920x1080 spec and applied to any variant
# resolution. ``pip`` layers the ad/full image BEHIND the (shrunken) video.
OVERLAY_TYPES = {"lband", "top_band", "bottom_band", "pip"}

POCKETS = {
    # video squeezes to top-right; band art fills the freed left/bottom area.
    "lband":       (1460 / 1920, 780 / 1080, 460 / 1920, 0.0),
    # video pushed down; band art across the top.
    "top_band":    (1.0,         930 / 1080, 0.0,         150 / 1080),
    # video pushed up; band art across the bottom.
    "bottom_band": (1.0,         840 / 1080, 0.0,         0.0),
    # video shrinks into a PIP window; ad art fills the rest (behind).
    "pip":         (820 / 1920,  460 / 1080, 1010 / 1920, 310 / 1080),
}


def _smoothstep_expr(duration: float, offset: float, t_in: float, t_out: float) -> str:
    """Eased squeeze factor e(g) in [0,1]: ramps 0->1 over [0,t_in], holds, then
    1->0 over [duration-t_out, duration]. ``g = t + offset`` is event-global time
    (``t`` is the segment-local frame time; ``offset`` is the segment's position
    within the event), so the animation is continuous across the event's
    segments without shifting the actual compositing timeline."""
    d_off = max(0.0, duration - t_out)
    g = f"(t+{offset:.4f})"
    s = (f"(clip({g}/{t_in:.3f},0,1)*"
         f"(1-clip(({g}-{d_off:.3f})/{t_out:.3f},0,1)))")
    return f"({s}*{s}*(3-2*{s}))"  # smoothstep


def build_squeeze_filter(vp: VideoParams, overlay_type: str, offset: float,
                         duration: float, t_in: float = 0.6, t_out: float = 0.6) -> str:
    """ffmpeg ``-filter_complex`` that squeezes the main video (input #0) into
    the overlay pocket and composites the overlay/ad art (input #1), animated by
    the eased factor e(t). ``offset`` is this segment's start time within the
    event (so the animation is continuous across the event's segments).

    Input pads: ``[0:v]`` video, ``[1:v]`` overlay image. Output pad: ``[outv]``.
    """
    W = vp.width or 1280
    H = vp.height or 720
    fw, fh, fx, fy = POCKETS.get(overlay_type, POCKETS["lband"])
    Wt = max(2, round(W * fw))
    Ht = max(2, round(H * fh))
    Xt = round(W * fx)
    Yt = round(H * fy)
    E = _smoothstep_expr(duration, offset, t_in, t_out)

    # Keep the segment 0-based and aligned with the bg/art (no compositing gap);
    # cross-segment continuity is handled by -output_ts_offset at mux time.
    pts = "setpts=PTS-STARTPTS"
    wexpr = f"floor(({W}-({E})*({W}-{Wt}))/2)*2"
    hexpr = f"floor(({H}-({E})*({H}-{Ht}))/2)*2"
    xexpr = f"({E})*{Xt}"
    yexpr = f"({E})*{Yt}"
    fps = vp.fps or 30

    v = (f"[0:v]{pts},scale=w='{wexpr}':h='{hexpr}':eval=frame,setsar=1[v]")
    bg = f"color=c=black:s={W}x{H}:r={fps:g}[bg]"
    art = f"[1:v]scale={W}:{H},format=rgba[art]"

    if overlay_type == "pip":
        # Ad art behind, shrunken video on top.
        return (f"{v};{bg};{art};[bg][art]overlay=0:0:shortest=1[base];"
                f"[base][v]overlay=x='{xexpr}':y='{yexpr}':eval=frame:shortest=1[outv]")
    # Bands: video on black, band art on top (its transparent center reveals video).
    return (f"{v};{bg};{art};[bg][v]overlay=x='{xexpr}':y='{yexpr}':eval=frame:shortest=1[m];"
            f"[m][art]overlay=0:0:eval=frame:shortest=1[outv]")
