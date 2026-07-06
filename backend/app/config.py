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
# player ever requests them. Kept small so the output window still mirrors the
# origin's live window (a large hold-back on a shallow-window origin would leave
# only 1-2 segments in our output and cause the player to rebuffer).
BUFFER_SEGMENTS = _int("OVERLAY_BUFFER_SEGMENTS", 3)
# Extra hold-back segments when the origin is HEVC (libx265 encoding is heavier,
# so it needs more lead time to avoid buffering during overlay transitions).
HEVC_EXTRA_BUFFER = _int("OVERLAY_HEVC_EXTRA_BUFFER", 3)

# How often the background pre-warm loop re-fetches each origin child playlist and
# queues overlay transcodes for segments that just appeared (seconds).
PREWARM_INTERVAL = float(os.environ.get("OVERLAY_PREWARM_INTERVAL", "1.5"))

# Max concurrent ffmpeg transcodes. Overlay windows fan out one job per variant
# per segment; enough workers to run all variants of a segment in parallel keeps
# the all-variants-ready gate from serializing.
MAX_TRANSCODE_WORKERS = _int("OVERLAY_MAX_WORKERS", 6)

# httpx timeout (seconds) for fetching origin manifests/segments.
ORIGIN_TIMEOUT = _int("OVERLAY_ORIGIN_TIMEOUT", 15)

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

# Encoder speed/quality for the overlaid (squeezed) segments. The squeeze
# animation + matching the origin codec (esp. HEVC) is CPU-heavy, so default to
# a fast preset; raise quality (slower preset / lower CRF) if you have headroom.
ENCODER_PRESET = os.environ.get("OVERLAY_ENCODER_PRESET", "ultrafast")
ENCODER_CRF = _int("OVERLAY_ENCODER_CRF", 23)
# Per-encode thread cap (0 = let ffmpeg choose). Short segments encode fastest
# with a few threads each so several variants can run in parallel without
# oversubscribing the CPU.
ENCODER_THREADS = _int("OVERLAY_ENCODER_THREADS", 0)
# Squeeze in/out animation durations (seconds).
SQUEEZE_IN = float(os.environ.get("OVERLAY_SQUEEZE_IN", "0.6"))
SQUEEZE_OUT = float(os.environ.get("OVERLAY_SQUEEZE_OUT", "0.6"))

# Verify TLS certificates when fetching origin manifests. Many live origins sit
# behind CDNs with chains the host can't validate, so this defaults to off.
# Set OVERLAY_VERIFY_TLS=1 to re-enable verification.
VERIFY_TLS = os.environ.get("OVERLAY_VERIFY_TLS", "0") not in ("0", "false", "False", "")


# --- MySQL ----------------------------------------------------------------
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = _int("DB_PORT", 3306)
DB_USER = os.environ.get("DB_USER", "root")
DB_PASS = os.environ.get("DB_PASS", "Piyush@23")
DB_NAME = os.environ.get("DB_NAME", "instream_overlay")
# Set DB_ENABLED=0 to run purely in-memory (no MySQL required).
DB_ENABLED = os.environ.get("DB_ENABLED", "1") not in ("0", "false", "False", "")


def db_url(include_db: bool = True) -> str:
    from urllib.parse import quote_plus
    auth = f"{quote_plus(DB_USER)}:{quote_plus(DB_PASS)}"
    base = f"mysql+pymysql://{auth}@{DB_HOST}:{DB_PORT}"
    return f"{base}/{DB_NAME}" if include_db else base


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
