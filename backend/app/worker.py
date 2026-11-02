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
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import config
from .codecs import VideoParams
from .transcode import transcode_segment

log = logging.getLogger("overlay.worker")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


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
    event_offset: float  # segment start relative to overlay start (may be < 0)
    duration: float      # total overlay-event duration (for the squeeze easing)
    seg_duration: float  # this segment's own length (for the art fade sync)
    mux_offset: float    # >=0 output_ts_offset so event segments stay continuous

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
        # When each key was last touched, so finished work can be forgotten.
        self._seen: dict[tuple, float] = {}
        self._tasks: list[asyncio.Task] = []
        self._status_cb: Optional[Callable[[dict], Awaitable[None]]] = None

    def set_status_callback(self, cb: Callable[[dict], Awaitable[None]]) -> None:
        self._status_cb = cb

    def prune(self, max_age: float) -> int:
        """Forget jobs finished longer than ``max_age`` ago.

        One key is kept per (channel, variant, overlay, segment), so without
        this the map grows for the life of the process — on a channel running
        continuously that is unbounded. Only settled work is dropped: a key
        still PENDING or PROCESSING is what the manifest gate reads, and
        removing it would make a segment look un-queued and be transcoded
        twice. The segments themselves are already gone by then (the retention
        sweep uses the same window), so a later request re-queues rather than
        trusting a stale READY."""
        cutoff = time.monotonic() - max_age
        done = (JobStatus.READY, JobStatus.FAILED)
        stale = [k for k, seen in self._seen.items()
                 if seen < cutoff and self._status.get(k) in done]
        for key in stale:
            self._status.pop(key, None)
            self._errors.pop(key, None)
            self._seen.pop(key, None)
        if stale:
            log.info("pruned %d settled transcode record(s); %d tracked",
                     len(stale), len(self._status))
        return len(stale)

    def tracked(self) -> int:
        """How many transcode records are held, so health can show the prune
        is keeping up rather than leaving it to be discovered as memory growth."""
        return len(self._status)

    def _set(self, key: tuple, status: JobStatus) -> None:
        """Record a job's status and when it was touched, so prune() can tell
        settled work from work the manifest gate is still waiting on."""
        self._status[key] = status
        self._seen[key] = time.monotonic()

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
            self._set(job.key, JobStatus.READY)
            return JobStatus.READY
        self._set(job.key, JobStatus.PENDING)
        self._queue.put_nowait(job)
        log.info("transcode QUEUED  ch=%s v%s seq=%s overlay=%s qdepth=%d origin=%s",
                 job.channel_id, job.variant_index, job.seq, job.overlay_id,
                 self._queue.qsize(), job.origin_url)
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
            self._set(job.key, JobStatus.PROCESSING)
            await self._emit(job, JobStatus.PROCESSING)
            started = time.monotonic()
            log.info("transcode STARTED ch=%s v%s seq=%s overlay=%s type=%s at=%s",
                     job.channel_id, job.variant_index, job.seq, job.overlay_id,
                     job.overlay_type, _now_iso())
            try:
                out = _out_path(*job.key)
                ok, err = await transcode_segment(
                    job.origin_url, job.overlay_image, job.vp, job.overlay_type,
                    job.event_offset, job.duration, job.seg_duration,
                    job.mux_offset, out)
                ms = int((time.monotonic() - started) * 1000)
                if ok:
                    self._set(job.key, JobStatus.READY)
                    size = out.stat().st_size if out.exists() else 0
                    log.info("transcode DONE    ch=%s v%s seq=%s overlay=%s in %dms "
                             "(%d bytes) at=%s", job.channel_id, job.variant_index,
                             job.seq, job.overlay_id, ms, size, _now_iso())
                    await self._emit(job, JobStatus.READY)
                else:
                    self._set(job.key, JobStatus.FAILED)
                    self._errors[job.key] = err
                    log.warning("transcode FAILED ch=%s v%s seq=%s after %dms",
                                job.channel_id, job.variant_index, job.seq, ms)
                    await self._emit(job, JobStatus.FAILED, err)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                self._set(job.key, JobStatus.FAILED)
                self._errors[job.key] = str(exc)
                log.exception("transcode EXCEPTION ch=%s v%s seq=%s: %s",
                              job.channel_id, job.variant_index, job.seq, exc)
                await self._emit(job, JobStatus.FAILED, str(exc))
            finally:
                self._queue.task_done()


pool = TranscodePool()
