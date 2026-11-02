"""Runtime configuration for the overlay backend.

Every value comes from the environment, loaded from a .env file beside the
backend if one exists, so the same code runs against the local origin simulator
or a real CDN origin without edits.

Secrets have NO defaults on purpose. A password written here would be committed
to the repository, and a default that silently works is a default nobody sets.
Anything secret is declared in SECRETS below and the app refuses to start
without it, naming what is missing. See .env.example.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

# Repo root = .../<repo>/backend/app/config.py -> parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Load backend/.env (then repo-root .env) into the environment.

    Real environment variables already set always win, so a container or a
    systemd unit can override the file without editing it.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional; env vars still work
        return
    for candidate in (BACKEND_ROOT / ".env", REPO_ROOT / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


_load_dotenv()


class ConfigError(RuntimeError):
    """A required setting is missing, so the process should not start."""


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# Where transcoded overlay segments and uploaded overlay images live. Defaults
# beside the backend rather than in a temp directory, which is wiped on reboot
# and differs per operating system.
DATA_DIR = Path(os.environ.get("OVERLAY_DATA_DIR", str(BACKEND_ROOT / "data")))
SEGMENT_DIR = DATA_DIR / "segments"
UPLOAD_DIR = DATA_DIR / "uploads"

# Public base URL of THIS backend, used when rewriting manifests so players and
# hls.js fetch our child manifests / overlaid segments from the right host. It
# must be the address a viewer can reach, not the address we bind to.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# How far behind the live edge we hold our manifest, in segments. This is the
# processing headroom that lets the worker transcode overlay segments before a
# player ever requests them. Kept small so the output window still mirrors the
# origin's live window (a large hold-back on a shallow-window origin would leave
# only 1-2 segments in our output and cause the player to rebuffer).
BUFFER_SEGMENTS = _int("OVERLAY_BUFFER_SEGMENTS", 6)
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
ENCODER_CRF = _int("OVERLAY_ENCODER_CRF", 28)
# Per-encode thread cap (0 = let ffmpeg choose). Short segments encode fastest
# with a few threads each so several variants can run in parallel without
# oversubscribing the CPU.
ENCODER_THREADS = _int("OVERLAY_ENCODER_THREADS", 1)
# Optional hardware video encoder for the overlay segments. "none" uses the
# software libx264/libx265 path; "nvenc" (NVIDIA) / "qsv" (Intel Quick Sync)
# offload the encode to the GPU to cut transcode time on a slow CPU. Both accept
# CPU-filtered frames as-is, so no filter changes are needed, and if the hardware
# encode fails at runtime we automatically retry that segment in software.
HWACCEL = os.environ.get("OVERLAY_HWACCEL", "qsv").strip().lower()
# Squeeze in/out animation durations (seconds).
SQUEEZE_IN = float(os.environ.get("OVERLAY_SQUEEZE_IN", "0.6"))
SQUEEZE_OUT = float(os.environ.get("OVERLAY_SQUEEZE_OUT", "0.6"))

# Verify TLS certificates when fetching origin manifests. On by default: an
# unverified fetch is a silent opening for anyone between us and the origin to
# substitute the stream. Set OVERLAY_VERIFY_TLS=0 only for a origin whose chain
# the host genuinely cannot validate, and treat that as temporary.
VERIFY_TLS = _bool("OVERLAY_VERIFY_TLS", True)


# --- retention -------------------------------------------------------------
# Transcoded overlay segments are written per variant per segment and are only
# ever needed while they sit inside the output DVR window. Without a sweep they
# accumulate until the disk fills, so anything older than this is deleted.
SEGMENT_RETENTION_SECONDS = _int("OVERLAY_SEGMENT_RETENTION", 1800)
# How often the sweeper runs (seconds).
RETENTION_INTERVAL = _int("OVERLAY_RETENTION_INTERVAL", 300)
# Free space below which health reports degraded (MB).
MIN_FREE_DISK_MB = _int("OVERLAY_MIN_FREE_DISK_MB", 2048)


# --- MySQL ----------------------------------------------------------------
# Set DB_ENABLED=0 to run purely in-memory (no MySQL required).
DB_ENABLED = _bool("DB_ENABLED", True)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = _int("DB_PORT", 3306)
DB_USER = os.environ.get("DB_USER", "root")
DB_NAME = os.environ.get("DB_NAME", "instream_overlay")
# No default: a password committed to source is a leaked password, and one that
# silently works is one nobody ever sets. Required only when the DB is enabled.
DB_PASS = os.environ.get("DB_PASS")

# name -> why it is needed, checked by require_settings() at startup.
SECRETS = {"DB_PASS": "the MySQL password (set DB_ENABLED=0 to run in-memory)"}


def require_settings() -> None:
    """Fail fast, naming every missing setting at once rather than crashing on
    first use with a driver error that does not say what to fix."""
    missing = []
    if DB_ENABLED:
        for name, why in SECRETS.items():
            if not os.environ.get(name):
                missing.append(f"  {name} — {why}")
    if missing:
        raise ConfigError(
            "Missing required configuration:\n" + "\n".join(missing)
            + f"\n\nSet them in {BACKEND_ROOT / '.env'} (copy .env.example) or"
              " in the environment."
        )


def db_url(include_db: bool = True) -> str:
    from urllib.parse import quote_plus
    auth = f"{quote_plus(DB_USER)}:{quote_plus(DB_PASS or '')}"
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
