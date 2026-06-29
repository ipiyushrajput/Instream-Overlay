"""Pydantic state + request/response models."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OverlayType(str, Enum):
    LBAND = "lband"
    TOP_BAND = "top_band"
    BOTTOM_BAND = "bottom_band"
    PIP = "pip"


class VariantInfo(BaseModel):
    index: int
    origin_uri: str
    inf_line: str
    codecs: str = ""
    resolution: Optional[str] = None
    frame_rate: Optional[str] = None
    bandwidth: Optional[int] = None
    # Derived video params used to match the overlay transcode to the variant.
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    profile: Optional[str] = None
    level: Optional[float] = None
    pix_fmt: str = "yuv420p"
    bitrate_kbps: Optional[int] = None
    has_audio: bool = True


class Channel(BaseModel):
    id: str
    name: str
    master_url: str
    variants: list[VariantInfo] = Field(default_factory=list)
    # Verbatim master-level lines (EXT-X-INDEPENDENT-SEGMENTS, etc.) preserved so
    # the output master mirrors the origin.
    master_other_lines: list[str] = Field(default_factory=list)
    # Audio/subtitle renditions (EXT-X-MEDIA): {idx, line, origin_uri}. Mirrored
    # through our /rendition endpoint with segments absolutized to origin.
    renditions: list[dict] = Field(default_factory=list)
    status: str = "active"   # "active" | "stopped"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OverlayEvent(BaseModel):
    id: str
    channel_id: str
    overlay_type: OverlayType = OverlayType.LBAND
    image_filename: str
    # Active window expressed in wall-clock (matches EXT-X-PROGRAM-DATE-TIME).
    start_pdt: datetime
    end_pdt: datetime
    # Positioning (used by lband/custom; ignored by full_frame).
    x_frac: float = 0.0
    y_frac: float = 0.0
    scale_frac: float = 1.0
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def covers(self, pdt: datetime) -> bool:
        return self.enabled and self.start_pdt <= pdt < self.end_pdt


# --- request bodies --------------------------------------------------------

class IngestRequest(BaseModel):
    master_url: str
    name: Optional[str] = None


class UpdateChannelRequest(BaseModel):
    name: Optional[str] = None
    master_url: Optional[str] = None  # changing this re-probes the variants


class CreateOverlayRequest(BaseModel):
    channel_id: str
    image_filename: str
    overlay_type: OverlayType = OverlayType.LBAND
    start_pdt: datetime
    end_pdt: datetime
    x_frac: float = 0.0
    y_frac: float = 0.0
    scale_frac: float = 1.0


class CreateOverlayRelativeRequest(BaseModel):
    """Convenience: start an overlay N seconds from 'now' for a given duration,
    resolved server-side against the live edge's program-date-time."""
    channel_id: str
    image_filename: str
    overlay_type: OverlayType = OverlayType.LBAND
    start_in_seconds: float = 12.0
    duration_seconds: float = 18.0
    x_frac: float = 0.0
    y_frac: float = 0.0
    scale_frac: float = 1.0
