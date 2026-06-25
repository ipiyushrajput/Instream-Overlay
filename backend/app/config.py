"""Runtime configuration for the overlay backend.

Values can be overridden with environment variables so the same code runs both
against the local origin simulator (in this environment) and against a real
CloudFront/transmit origin on the user's own machine.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

# Repo root = .../<repo>/backend/app/config.py -> parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]


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
# Logs live in <repo>/logs by default (sibling of backend/ and frontend/).
LOG_DIR = Path(os.environ.get("OVERLAY_LOG_DIR", REPO_ROOT / "logs"))


def ensure_dirs() -> None:
    SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    """Configure the ``overlay`` logger with a stdout handler (so logs show in
    the uvicorn console) plus a rotating file handler in ``<repo>/logs``. The
    same file handler is attached to uvicorn's loggers so HTTP access/errors
    land in the file too."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "backend.log", maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(fmt)

    logger = logging.getLogger("overlay")
    if not any(isinstance(h, logging.StreamHandler) and
               not isinstance(h, logging.handlers.RotatingFileHandler)
               for h in logger.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    # Avoid duplicate file handlers on reload.
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
        logger.addHandler(file_handler)
    logger.setLevel(level)
    logger.propagate = False

    # Capture uvicorn's request/error logs in the same file.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        ul = logging.getLogger(name)
        if not any(isinstance(h, logging.handlers.RotatingFileHandler)
                   for h in ul.handlers):
            ul.addHandler(file_handler)
    return logger
