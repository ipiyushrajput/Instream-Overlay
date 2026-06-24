"""In-memory state store for channels and overlay events.

Kept intentionally simple (process-local dicts). Swapping in SQLite later only
touches this module.
"""
from __future__ import annotations

import threading
import uuid

from .models import Channel, OverlayEvent


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._channels: dict[str, Channel] = {}
        self._overlays: dict[str, OverlayEvent] = {}

    # channels
    def add_channel(self, channel: Channel) -> Channel:
        with self._lock:
            self._channels[channel.id] = channel
        return channel

    def get_channel(self, channel_id: str) -> Channel | None:
        return self._channels.get(channel_id)

    def list_channels(self) -> list[Channel]:
        return list(self._channels.values())

    # overlays
    def add_overlay(self, overlay: OverlayEvent) -> OverlayEvent:
        with self._lock:
            self._overlays[overlay.id] = overlay
        return overlay

    def get_overlay(self, overlay_id: str) -> OverlayEvent | None:
        return self._overlays.get(overlay_id)

    def delete_overlay(self, overlay_id: str) -> bool:
        with self._lock:
            return self._overlays.pop(overlay_id, None) is not None

    def overlays_for_channel(self, channel_id: str) -> list[OverlayEvent]:
        return [o for o in self._overlays.values() if o.channel_id == channel_id]


def new_id() -> str:
    return uuid.uuid4().hex[:24]


store = Store()
