"""Built-in default overlay band art so operators can test without uploading.

The four defaults point at user-supplied image URLs (editable below). On startup
we download each into the uploads dir so the transcoder has a local file; if a
URL can't be fetched we fall back to a generated placeholder band so the presets
still work offline.
"""
from __future__ import annotations

import logging
import subprocess

import httpx

from . import config

log = logging.getLogger("overlay.defaults")

# overlay_type -> (filename, label, source URL, placeholder drawbox chain)
_DEFAULTS = {
    "lband": ("default_lband.png", "L-band",
              "https://d2b0puv2znzrgu.cloudfront.net/Overlays/l-band.png",
              "drawbox=x=0:y=0:w=460:h=1080:color=0x1E3A8A@0.88:t=fill,"
              "drawbox=x=0:y=780:w=1920:h=300:color=0x1E3A8A@0.88:t=fill"),
    "top_band": ("default_top_band.png", "Top band",
                 "https://d2b0puv2znzrgu.cloudfront.net/Overlays/top-band.png",
                 "drawbox=x=0:y=0:w=1920:h=150:color=0x7C3AED@0.9:t=fill"),
    "bottom_band": ("default_bottom_band.png", "Bottom band",
                    "https://d2b0puv2znzrgu.cloudfront.net/Overlays/bottom-band.png",
                    "drawbox=x=0:y=840:w=1920:h=240:color=0x059669@0.9:t=fill"),
    "pip": ("default_pip.png", "PIP ad",
            "https://d2b0puv2znzrgu.cloudfront.net/Overlays/pip.png",
            "drawbox=x=0:y=0:w=1920:h=1080:color=0xB91C1C@0.96:t=fill"),
}


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _download(url: str, dest) -> bool:
    try:
        with httpx.Client(follow_redirects=True, timeout=20, verify=False,
                          headers={"User-Agent": _UA, "Accept": "image/*,*/*"}) as c:
            r = c.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if r.content and ("image" in ctype or len(r.content) > 200):
            dest.write_bytes(r.content)
            log.info("downloaded default image %s (%d bytes, %s)", dest.name, len(r.content), ctype)
            return True
        log.warning("default image %s returned non-image content-type=%s", url, ctype)
    except Exception as exc:  # noqa: BLE001
        log.warning("default image download failed (%s): %s", url, exc)
    return False


def _placeholder(chain: str, dest) -> None:
    cmd = [config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "color=c=black@0.0:s=1920x1080,format=rgba",
           "-vf", chain, "-frames:v", "1", str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not generate placeholder %s: %s", dest, exc)


def ensure_default_overlays() -> None:
    """Refresh the default band art from the configured URLs. Always re-download
    so an updated URL (or a previous black placeholder) is replaced; only fall
    back to a generated placeholder when the download fails AND we have nothing."""
    config.ensure_dirs()
    for _otype, (fname, _label, url, chain) in _DEFAULTS.items():
        dest = config.UPLOAD_DIR / fname
        if _download(url, dest):
            continue
        if not (dest.exists() and dest.stat().st_size > 0):
            _placeholder(chain, dest)
            log.info("using generated placeholder for %s (download failed)", fname)
    log.info("default overlay art ready in %s", config.UPLOAD_DIR)


def list_defaults() -> list[dict]:
    out = []
    for otype, (fname, label, url, _chain) in _DEFAULTS.items():
        if (config.UPLOAD_DIR / fname).exists():
            out.append({
                "overlay_type": otype, "label": label,
                "image_filename": fname, "source_url": url,
                "url": f"{config.PUBLIC_BASE_URL}/uploads/{fname}",
            })
    return out
