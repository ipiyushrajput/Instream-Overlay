"""Runtime configuration for the overlay backend.

Values can be overridden with environment variables so the same code runs both
against the local origin simulator (in this environment) and against a real
CloudFront/transmit origin on the user's own machine.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Where transcoded overlay segments and uploaded overlay images live.
DATA_DIR = Path(os.environ.get("OVERLAY_DATA_DIR", "/tmp/instream-overlay-data"))
SEGMENT_DIR = DATA_DIR / "segments"
UPLOAD_DIR = DATA_DIR / "uploads"

# Public base URL of THIS backend, used when rewriting manifests so players and
# hls.js fetch our child manifests / overlaid segments from the right host.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# How far behind the live edge we hold our manifest, in segments. This is the
# processing headroom that lets the worker transcode overlay segments before a
# player ever requests them. The user explicitly accepts this added delay.
BUFFER_SEGMENTS = _int("OVERLAY_BUFFER_SEGMENTS", 3)

# Max concurrent ffmpeg transcodes.
MAX_TRANSCODE_WORKERS = _int("OVERLAY_MAX_WORKERS", 4)

# httpx timeout (seconds) for fetching origin manifests/segments.
ORIGIN_TIMEOUT = _int("OVERLAY_ORIGIN_TIMEOUT", 15)

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

# Verify TLS certificates when fetching origin manifests. Many live origins sit
# behind CDNs with chains the host can't validate, so this defaults to off.
# Set OVERLAY_VERIFY_TLS=1 to re-enable verification.
VERIFY_TLS = os.environ.get("OVERLAY_VERIFY_TLS", "0") not in ("0", "false", "False", "")


LOG_LEVEL = os.environ.get("OVERLAY_LOG_LEVEL", "INFO").upper()


def ensure_dirs() -> None:
    SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    """Configure the ``overlay`` logger namespace with its own stdout handler so
    our logs always show alongside uvicorn's, independent of root config."""
    logger = logging.getLogger("overlay")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S"))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    return logger
