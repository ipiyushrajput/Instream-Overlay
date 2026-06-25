"""Async transcode worker pool + overlay-segment cache + discontinuity tracking.

The manifest builder asks the pool whether the overlaid version of a given
segment is ready. If it is, the manifest points at our copy; if not, the job is
queued and the manifest falls back to the origin segment for that beat so
playback never stalls.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import config
from .codecs import VideoParams
from .transcode import transcode_segment

log = logging.getLogger("overlay.worker")


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass
class Job:
    channel_id: str
    variant_index: int
    overlay_id: str
    seq: int
    origin_url: str
    overlay_image: str
    vp: VideoParams
    overlay_type: str
    x_frac: float
    y_frac: float
    scale_frac: float

    @property
    def key(self) -> tuple:
        return (self.channel_id, self.variant_index, self.overlay_id, self.seq)


def _out_path(channel_id: str, variant_index: int, overlay_id: str, seq: int) -> Path:
    return config.SEGMENT_DIR / channel_id / str(variant_index) / overlay_id / f"{seq}.ts"


class TranscodePool:
    def __init__(self, workers: int = config.MAX_TRANSCODE_WORKERS) -> None:
        self._workers = workers
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._status: dict[tuple, JobStatus] = {}
        self._errors: dict[tuple, str] = {}
        self._tasks: list[asyncio.Task] = []
        self._status_cb: Optional[Callable[[dict], Awaitable[None]]] = None

    def set_status_callback(self, cb: Callable[[dict], Awaitable[None]]) -> None:
        self._status_cb = cb

    def start(self) -> None:
        for _ in range(self._workers):
            self._tasks.append(asyncio.create_task(self._run()))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    def status_of(self, channel_id: str, variant_index: int, overlay_id: str,
                  seq: int) -> Optional[JobStatus]:
        return self._status.get((channel_id, variant_index, overlay_id, seq))

    def error_of(self, channel_id: str, variant_index: int, overlay_id: str,
                 seq: int) -> Optional[str]:
        return self._errors.get((channel_id, variant_index, overlay_id, seq))

    def segment_path(self, channel_id: str, variant_index: int, overlay_id: str,
                     seq: int) -> Path:
        return _out_path(channel_id, variant_index, overlay_id, seq)

    def ensure(self, job: Job) -> JobStatus:
        """Return the current status, queuing the job the first time it's seen.

        Reuses an already-rendered file across restarts when present on disk.
        """
        st = self._status.get(job.key)
        if st is not None:
            return st
        out = _out_path(*job.key)
        if out.exists() and out.stat().st_size > 0:
            self._status[job.key] = JobStatus.READY
            return JobStatus.READY
        self._status[job.key] = JobStatus.PENDING
        self._queue.put_nowait(job)
        log.info("queued transcode ch=%s v%s seq=%s overlay=%s origin=%s",
                 job.channel_id, job.variant_index, job.seq, job.overlay_id,
                 job.origin_url)
        return JobStatus.PENDING

    async def _emit(self, job: Job, status: JobStatus, error: str = "") -> None:
        if self._status_cb is None:
            return
        await self._status_cb({
            "type": "segment_status",
            "channel_id": job.channel_id,
            "variant_index": job.variant_index,
            "overlay_id": job.overlay_id,
            "seq": job.seq,
            "status": status.value,
            "error": error[:300],
        })

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            self._status[job.key] = JobStatus.PROCESSING
            await self._emit(job, JobStatus.PROCESSING)
            started = time.monotonic()
            try:
                out = _out_path(*job.key)
                ok, err = await transcode_segment(
                    job.origin_url, job.overlay_image, job.vp, job.overlay_type,
                    job.x_frac, job.y_frac, job.scale_frac, out)
                ms = int((time.monotonic() - started) * 1000)
                if ok:
                    self._status[job.key] = JobStatus.READY
                    size = out.stat().st_size if out.exists() else 0
                    log.info("transcoded ch=%s v%s seq=%s in %dms (%d bytes)",
                             job.channel_id, job.variant_index, job.seq, ms, size)
                    await self._emit(job, JobStatus.READY)
                else:
                    self._status[job.key] = JobStatus.FAILED
                    self._errors[job.key] = err
                    log.warning("transcode FAILED ch=%s v%s seq=%s after %dms",
                                job.channel_id, job.variant_index, job.seq, ms)
                    await self._emit(job, JobStatus.FAILED, err)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                self._status[job.key] = JobStatus.FAILED
                self._errors[job.key] = str(exc)
                log.exception("transcode EXCEPTION ch=%s v%s seq=%s: %s",
                              job.channel_id, job.variant_index, job.seq, exc)
                await self._emit(job, JobStatus.FAILED, str(exc))
            finally:
                self._queue.task_done()


class DiscontinuityTracker:
    """Tracks how many *injected* discontinuities have scrolled out of the live
    window for one (channel, variant) so EXT-X-DISCONTINUITY-SEQUENCE stays
    correct as the playlist slides.

    Origin-native discontinuities are accounted for by the origin's own
    DISCONTINUITY-SEQUENCE; we only add the ones we introduce.
    """
    def __init__(self) -> None:
        self._injected: dict[int, bool] = {}   # seq -> had injected discontinuity
        self._scrolled_out = 0

    def observe(self, window_seqs_with_inject: dict[int, bool], window_min_seq: int) -> int:
        # Record current window's injected-discontinuity flags.
        for seq, has in window_seqs_with_inject.items():
            self._injected[seq] = has
        # Anything below the window's first seq has scrolled out for good.
        for seq in sorted(s for s in self._injected if s < window_min_seq):
            if self._injected.pop(seq):
                self._scrolled_out += 1
        return self._scrolled_out


pool = TranscodePool()
