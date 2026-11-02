"""Retention: the sweep that stops transcoded segments filling the disk, and
the pruning that stops the transcode bookkeeping growing without end."""
import os
import time

from app import retention
from app.worker import JobStatus, TranscodePool


def _aged(path, seconds):
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_sweep_removes_aged_segments_and_keeps_fresh_ones(tmp_path):
    old = tmp_path / "ch" / "0" / "ov" / "100.ts"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x" * 1000)
    new = old.parent / "101.ts"
    new.write_bytes(b"y" * 500)
    _aged(old, 7200)

    removed, reclaimed = retention.sweep_segments(tmp_path, max_age=1800)

    assert removed == 1
    assert reclaimed == 1000
    assert not old.exists()
    assert new.exists(), "a segment still inside the window was deleted"


def test_sweep_collects_emptied_directories_but_keeps_the_root(tmp_path):
    seg = tmp_path / "ch" / "0" / "ov" / "100.ts"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"x")
    _aged(seg, 7200)

    retention.sweep_segments(tmp_path, max_age=1800)

    assert not (tmp_path / "ch").exists()
    assert tmp_path.exists(), "the writer expects the root to stay"


def test_sweep_ignores_files_it_does_not_own(tmp_path):
    keep = tmp_path / "notes.txt"
    keep.write_text("not a segment")
    _aged(keep, 99999)

    removed, _ = retention.sweep_segments(tmp_path, max_age=1800)

    assert removed == 0
    assert keep.exists()


def test_sweep_on_a_missing_directory_is_a_no_op(tmp_path):
    assert retention.sweep_segments(tmp_path / "nope", max_age=1800) == (0, 0)


def test_prune_forgets_settled_work():
    pool = TranscodePool()
    key = ("ch", 0, "ov", 1)
    pool._set(key, JobStatus.READY)
    pool._seen[key] = time.monotonic() - 10_000

    assert pool.prune(max_age=1800) == 1
    assert pool.tracked() == 0


def test_prune_keeps_work_the_manifest_gate_is_waiting_on():
    """A PENDING or PROCESSING key is what tells the gate a segment is already
    queued. Dropping it would make the segment look un-queued and be
    transcoded a second time."""
    pool = TranscodePool()
    for status in (JobStatus.PENDING, JobStatus.PROCESSING):
        key = ("ch", 0, "ov", status.value)
        pool._set(key, status)
        pool._seen[key] = time.monotonic() - 10_000

    assert pool.prune(max_age=1800) == 0
    assert pool.tracked() == 2


def test_prune_keeps_recent_work():
    pool = TranscodePool()
    key = ("ch", 0, "ov", 1)
    pool._set(key, JobStatus.READY)  # just now

    assert pool.prune(max_age=1800) == 0
    assert pool.tracked() == 1
