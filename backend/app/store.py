"""In-memory state store for channels and overlay events.

Kept intentionally simple (process-local dicts). Swapping in SQLite later only
touches this module.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime

from . import db
from .models import Channel, OverlayEvent

log = logging.getLogger("overlay.store")


def _to_dt(v):
    if isinstance(v, datetime) or v is None:
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._channels: dict[str, Channel] = {}
        self._overlays: dict[str, OverlayEvent] = {}
        self._injected: dict[str, set] = {}   # overlay_id -> set of injected seqs
        # channel_id -> {seq -> overlay_id or None}: the single authoritative
        # overlay/origin decision per segment, committed by the first video
        # variant to freeze that seq. All other variants and the audio/subtitle
        # renditions follow it, so every variant agrees on discontinuity placement.
        self._seq_overlay: dict[str, dict[int, "str | None"]] = {}
        self._sessions: list[dict] = []       # viewer sessions (master hits)
        self._deliveries: dict[str, dict] = {}  # overlay_id -> delivery record

    def load_from_db(self) -> None:
        """Hydrate the in-memory store from MySQL on startup."""
        for data in db.load_channels():
            try:
                ch = Channel.model_validate(data)
                self._channels[ch.id] = ch
            except Exception as exc:  # noqa: BLE001
                log.warning("skip bad channel row: %s", exc)
        for data in db.load_overlays():
            try:
                ov = OverlayEvent.model_validate(data)
                self._overlays[ov.id] = ov
            except Exception as exc:  # noqa: BLE001
                log.warning("skip bad overlay row: %s", exc)
        self._sessions = db.load_sessions()
        for d in db.load_deliveries():
            self._deliveries[d["overlay_id"]] = d
        if self._channels or self._overlays:
            log.info("loaded %d channel(s), %d overlay(s), %d session(s) from DB",
                     len(self._channels), len(self._overlays), len(self._sessions))

    # insights: sessions + deliveries
    def add_session(self, row: dict) -> None:
        with self._lock:
            self._sessions.append(row)
        db.add_session({**row, "session_start": _to_dt(row.get("session_start"))})

    def sessions_for_channel(self, channel_id: str) -> list[dict]:
        return [s for s in self._sessions if s.get("channel_id") == channel_id]

    def record_delivery(self, overlay_id: str, channel_id: str,
                        overlay_type: str, segments: int, status: str) -> None:
        rec = {"overlay_id": overlay_id, "channel_id": channel_id,
               "overlay_type": overlay_type, "segments": segments, "status": status}
        with self._lock:
            self._deliveries[overlay_id] = rec
        db.upsert_delivery(rec)

    def deliveries_for_channel(self, channel_id: str) -> list[dict]:
        return [d for d in self._deliveries.values() if d.get("channel_id") == channel_id]

    def all_deliveries(self) -> list[dict]:
        return list(self._deliveries.values())

    def all_sessions(self) -> list[dict]:
        return list(self._sessions)

    # channels
    def add_channel(self, channel: Channel) -> Channel:
        with self._lock:
            self._channels[channel.id] = channel
        db.upsert_channel(channel)
        return channel

    def update_channel(self, channel: Channel) -> Channel:
        with self._lock:
            self._channels[channel.id] = channel
        db.upsert_channel(channel)
        return channel

    def get_channel(self, channel_id: str) -> Channel | None:
        return self._channels.get(channel_id)

    def list_channels(self) -> list[Channel]:
        return list(self._channels.values())

    def delete_channel(self, channel_id: str) -> bool:
        with self._lock:
            existed = self._channels.pop(channel_id, None) is not None
            for oid in [o.id for o in self._overlays.values()
                        if o.channel_id == channel_id]:
                self._overlays.pop(oid, None)
                self._injected.pop(oid, None)
                self._deliveries.pop(oid, None)
            self._sessions = [s for s in self._sessions if s.get("channel_id") != channel_id]
            self._seq_overlay.pop(channel_id, None)
        db.delete_channel(channel_id)
        db.delete_channel_sessions(channel_id)
        return existed

    # overlays
    def add_overlay(self, overlay: OverlayEvent) -> OverlayEvent:
        with self._lock:
            self._overlays[overlay.id] = overlay
        db.upsert_overlay(overlay)
        return overlay

    def get_overlay(self, overlay_id: str) -> OverlayEvent | None:
        return self._overlays.get(overlay_id)

    def delete_overlay(self, overlay_id: str) -> bool:
        with self._lock:
            self._injected.pop(overlay_id, None)
            existed = self._overlays.pop(overlay_id, None) is not None
        db.delete_overlay(overlay_id)
        return existed

    def overlays_for_channel(self, channel_id: str) -> list[OverlayEvent]:
        return [o for o in self._overlays.values() if o.channel_id == channel_id]

    def mark_injected(self, overlay_id: str, seq: int) -> None:
        with self._lock:
            self._injected.setdefault(overlay_id, set()).add(seq)

    def injected_count(self, overlay_id: str) -> int:
        return len(self._injected.get(overlay_id, ()))

    def record_seq_decision(self, channel_id: str, seq: int,
                            overlay_id: "str | None") -> "str | None":
        """Commit the overlay/origin decision for one segment sequence, first
        writer wins, and return the effective (committed) decision. The first
        video variant to freeze ``seq`` decides whether it's overlaid (returns the
        overlay id) or plain origin (None); later variants pass their own guess
        but get back the committed value, so all variants stay consistent."""
        with self._lock:
            d = self._seq_overlay.setdefault(channel_id, {})
            if seq not in d:
                d[seq] = overlay_id
                if len(d) > 4000:  # keep bounded: drop the oldest sequences
                    for s in sorted(d)[:len(d) - 3000]:
                        d.pop(s, None)
            return d[seq]

    def peek_seq_decision(self, channel_id: str, seq: int) -> "tuple[bool, str | None]":
        """(committed?, overlay_id). Renditions read this to mirror the video's
        discontinuities exactly, waiting until the video has committed the seq."""
        d = self._seq_overlay.get(channel_id, {})
        return (seq in d, d.get(seq))


def new_id() -> str:
    return uuid.uuid4().hex[:24]


store = Store()
