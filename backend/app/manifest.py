"""HLS manifest parsing and rewriting.

We deliberately hand-roll a small line-oriented parser instead of pulling in an
m3u8 library so we keep byte-level control over tag fidelity — in particular
`#EXT-X-PROGRAM-DATE-TIME`, `#EXT-X-DISCONTINUITY` and the
`#EXT-X-DISCONTINUITY-SEQUENCE` bookkeeping that has to stay correct as the
live window slides.

Two rewrites happen here:

* **Master**  -> point each variant at our own child-manifest endpoint.
* **Media**   -> serve normal segments as *absolute origin URLs* (pass-through)
                 and splice overlaid segments (from our server) between
                 `#EXT-X-DISCONTINUITY` markers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

_ATTR_RE = re.compile(r'([A-Z0-9\-]+)=("[^"]*"|[^,]*)')


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- data structures -------------------------------------------------------

@dataclass
class MasterVariant:
    attributes: dict          # raw STREAM-INF attributes
    uri: str                  # absolute origin URI of the child manifest
    inf_line: str             # the original "#EXT-X-STREAM-INF:..." line

    @property
    def resolution(self) -> Optional[str]:
        return self.attributes.get("RESOLUTION")

    @property
    def codecs(self) -> str:
        return self.attributes.get("CODECS", "").strip('"')

    @property
    def frame_rate(self) -> Optional[str]:
        return self.attributes.get("FRAME-RATE")

    @property
    def bandwidth(self) -> Optional[int]:
        for key in ("BANDWIDTH", "AVERAGE-BANDWIDTH"):
            if key in self.attributes:
                try:
                    return int(self.attributes[key])
                except ValueError:
                    pass
        return None


@dataclass
class MediaSegment:
    uri: str                          # absolute origin URI
    duration: float
    seq: int                          # media sequence number
    pdt: Optional[str] = None         # ISO8601 program-date-time
    discontinuity_before: bool = False
    extra_tags: list = field(default_factory=list)  # passthrough tags (#EXT-X-BYTERANGE etc.)


@dataclass
class MediaPlaylist:
    version: int = 3
    target_duration: int = 6
    media_sequence: int = 0
    discontinuity_sequence: int = 0
    segments: list = field(default_factory=list)
    endlist: bool = False
    header_tags: list = field(default_factory=list)  # e.g. #EXT-X-MAP, #EXT-X-KEY


def parse_attributes(line: str) -> dict:
    body = line.split(":", 1)[1] if ":" in line else ""
    return {m.group(1): m.group(2).strip('"') for m in _ATTR_RE.finditer(body)}


# --- master ----------------------------------------------------------------

def parse_master(text: str, base_url: str) -> list[MasterVariant]:
    variants: list[MasterVariant] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            attrs = parse_attributes(line)
            # The URI is the next non-comment line.
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j < len(lines):
                uri = urljoin(base_url, lines[j].strip())
                variants.append(MasterVariant(attributes=attrs, uri=uri, inf_line=line))
            i = j + 1
        else:
            i += 1
    return variants


def render_master(variants: list[MasterVariant], child_url_for) -> str:
    """Render our master. ``child_url_for(index, variant)`` returns the URI we
    want players to use for that variant's child manifest."""
    out = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for idx, v in enumerate(variants):
        out.append(v.inf_line)
        out.append(child_url_for(idx, v))
    return "\n".join(out) + "\n"


def is_master(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


# --- media -----------------------------------------------------------------

def parse_media(text: str, base_url: str) -> MediaPlaylist:
    pl = MediaPlaylist()
    lines = text.splitlines()
    seq = 0
    pending_duration = 0.0
    pending_pdt: Optional[str] = None
    pending_disc = False
    pending_extra: list = []
    seen_first_seq = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-VERSION:"):
            pl.version = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            pl.target_duration = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            pl.media_sequence = int(line.split(":", 1)[1])
            seq = pl.media_sequence
            seen_first_seq = True
        elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            pl.discontinuity_sequence = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MAP") or line.startswith("#EXT-X-KEY"):
            pl.header_tags.append(line)
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            pending_disc = True
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pending_pdt = line.split(":", 1)[1]
        elif line.startswith("#EXTINF:"):
            dur = line.split(":", 1)[1].split(",")[0]
            try:
                pending_duration = float(dur)
            except ValueError:
                pending_duration = 0.0
        elif line.startswith("#EXT-X-ENDLIST"):
            pl.endlist = True
        elif line.startswith("#EXT-X-BYTERANGE"):
            pending_extra.append(line)
        elif line.startswith("#"):
            # Unknown tag: ignore for now (kept simple on purpose).
            continue
        else:
            # A media URI line -> close out the current segment.
            if not seen_first_seq:
                seen_first_seq = True
            pl.segments.append(MediaSegment(
                uri=urljoin(base_url, line),
                duration=pending_duration,
                seq=seq,
                pdt=pending_pdt,
                discontinuity_before=pending_disc,
                extra_tags=pending_extra,
            ))
            seq += 1
            pending_duration = 0.0
            pending_pdt = None
            pending_disc = False
            pending_extra = []

    _forward_fill_pdt(pl)
    return pl


def _forward_fill_pdt(pl: MediaPlaylist) -> None:
    """Give every segment an effective PROGRAM-DATE-TIME.

    Per the HLS spec, ``#EXT-X-PROGRAM-DATE-TIME`` is an *anchor* that applies to
    the following segment; later segments' wall-clock is that anchor plus the
    accumulated ``EXTINF`` durations. Many origins only stamp the first segment
    (or one per discontinuity), so we reconstruct the rest here. We re-anchor on
    every explicit tag and (best effort) carry the timeline across boundaries.

    Segments that already carry an explicit tag keep their original string; only
    the gaps are filled (with ISO8601 values), so overlay matching and the
    rendered output both see a complete timeline.
    """
    running: Optional[datetime] = None
    for seg in pl.segments:
        explicit = _parse_iso(seg.pdt) if seg.pdt else None
        if explicit is not None:
            running = explicit
        elif running is not None:
            seg.pdt = running.isoformat()
        if running is not None:
            running = running + timedelta(seconds=seg.duration or 0.0)


def render_media(pl: MediaPlaylist) -> str:
    """Render a media playlist from our (possibly rewritten) model."""
    out = ["#EXTM3U", f"#EXT-X-VERSION:{pl.version}",
           f"#EXT-X-TARGETDURATION:{pl.target_duration}",
           f"#EXT-X-MEDIA-SEQUENCE:{pl.media_sequence}",
           f"#EXT-X-DISCONTINUITY-SEQUENCE:{pl.discontinuity_sequence}"]
    out.extend(pl.header_tags)
    for seg in pl.segments:
        if seg.discontinuity_before:
            out.append("#EXT-X-DISCONTINUITY")
        if seg.pdt:
            out.append(f"#EXT-X-PROGRAM-DATE-TIME:{seg.pdt}")
        out.extend(seg.extra_tags)
        out.append(f"#EXTINF:{seg.duration:.6f},")
        out.append(seg.uri)
    if pl.endlist:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"
