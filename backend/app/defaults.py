"""Built-in default overlay band art so operators can test without uploading.

Each default is a 1920x1080 RGBA PNG that is transparent inside the video
"pocket" and opaque (a coloured band / full ad frame) in the freed area, matching
the squeeze pockets in ``codecs.POCKETS``. They are generated once into the
uploads dir and exposed as presets the frontend can select directly.
"""
from __future__ import annotations

import logging
import subprocess

from . import config

log = logging.getLogger("overlay.defaults")

# filename -> (overlay_type, label, drawbox filter chain on a transparent base)
_DEFAULTS = {
    "default_lband.png": (
        "lband", "L-band",
        "drawbox=x=0:y=0:w=460:h=1080:color=0x1E3A8A@0.88:t=fill,"
        "drawbox=x=0:y=780:w=1920:h=300:color=0x1E3A8A@0.88:t=fill"),
    "default_top_band.png": (
        "top_band", "Top band",
        "drawbox=x=0:y=0:w=1920:h=150:color=0x7C3AED@0.9:t=fill"),
    "default_bottom_band.png": (
        "bottom_band", "Bottom band",
        "drawbox=x=0:y=840:w=1920:h=240:color=0x059669@0.9:t=fill"),
    "default_pip.png": (
        "pip", "PIP ad",
        "drawbox=x=0:y=0:w=1920:h=1080:color=0xB91C1C@0.96:t=fill"),
}


def ensure_default_overlays() -> None:
    config.ensure_dirs()
    for fname, (_otype, _label, chain) in _DEFAULTS.items():
        dest = config.UPLOAD_DIR / fname
        if dest.exists() and dest.stat().st_size > 0:
            continue
        cmd = [config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "color=c=black@0.0:s=1920x1080,format=rgba",
               "-vf", chain, "-frames:v", "1", str(dest)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not generate default %s: %s", fname, exc)
    log.info("default overlay art ready in %s", config.UPLOAD_DIR)


def list_defaults() -> list[dict]:
    out = []
    for fname, (otype, label, _chain) in _DEFAULTS.items():
        if (config.UPLOAD_DIR / fname).exists():
            out.append({
                "overlay_type": otype, "label": label,
                "image_filename": fname,
                "url": f"{config.PUBLIC_BASE_URL}/uploads/{fname}",
            })
    return out
