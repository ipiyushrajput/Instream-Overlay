"""Disk retention for transcoded overlay segments.

Every overlay writes one .ts file per variant per covered segment. Those files
are only ever needed while they sit inside the output DVR window — once the
window has rolled past, nothing will request them again. Left alone they
accumulate for as long as the service runs, and on a channel that runs
continuously the disk fills and the service stops serving.

The sweep is deliberately based on file age rather than on the overlay records:
age is true whatever the store believes, so a segment is still collected after a
restart, after an overlay row is deleted, or after a channel is removed — the
cases where a store-driven sweep would leak exactly the files nobody owns any
more.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from . import config

log = logging.getLogger("overlay.retention")


def sweep_segments(root: Path | None = None, max_age: int | None = None) -> tuple[int, int]:
    """Delete transcoded segments older than the retention window.

    Returns (files_removed, bytes_reclaimed). Never raises: a sweep that cannot
    read or unlink one file must not take the service down, so problems are
    logged and the sweep moves on.
    """
    root = root or config.SEGMENT_DIR
    max_age = config.SEGMENT_RETENTION_SECONDS if max_age is None else max_age
    if not root.exists():
        return (0, 0)

    cutoff = time.time() - max_age
    removed = reclaimed = 0
    for path in root.rglob("*.ts"):
        try:
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            size = stat.st_size
            path.unlink()
            removed += 1
            reclaimed += size
        except FileNotFoundError:
            continue  # a concurrent sweep or a writer got there first
        except OSError as exc:
            log.debug("could not remove %s: %s", path, exc)

    _prune_empty_dirs(root)
    if removed:
        log.info("retention: removed %d segment(s), reclaimed %.1f MB (older than %ds)",
                 removed, reclaimed / 1_048_576, max_age)
    return (removed, reclaimed)


def _prune_empty_dirs(root: Path) -> None:
    """Remove directories left empty by the sweep, deepest first so a parent
    emptied by its children is collected in the same pass. ``root`` itself is
    kept: the writer expects it to exist."""
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            next(path.iterdir())
        except StopIteration:
            try:
                path.rmdir()
            except OSError:
                pass
        except OSError:
            continue


def disk_free_mb(path: Path | None = None) -> float | None:
    """Free space on the volume holding the data directory, in MB. None when it
    cannot be determined, so a caller can report 'unknown' rather than a wrong
    number."""
    import shutil
    try:
        return shutil.disk_usage(path or config.DATA_DIR).free / 1_048_576
    except OSError:
        return None
