"""HLS manifest parsing and rewriting with full tag fidelity.

We hand-roll a line-oriented parser so we keep byte-level control over the tags
we must manage (PROGRAM-DATE-TIME, DISCONTINUITY, MEDIA/DISCONTINUITY-SEQUENCE)
while passing **everything else through verbatim** — SCTE-35 / CUE-OUT /
CUE-IN / OATCLS / DATERANGE / KEY / MAP, plus master-level INDEPENDENT-SEGMENTS
and EXT-X-MEDIA (audio/subtitle) renditions.

Two rewrites happen here:

* **Master**  -> preserve all header lines (incl. EXT-X-MEDIA with its URI
                 rewritten to the absolute origin), point each video variant at
                 our child-manifest endpoint.
* **Media**   -> serve normal segments as absolute origin URLs (pass-through),
                 carry every per-segment tag, and splice overlaid segments
                 between DISCONTINUITY markers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

_ATTR_RE = re.compile(r'([A-Z0-9\-]+)=("[^"]*"|[^,]*)')
_URI_ATTR_RE = re.compile(r'URI="([^"]*)"')

# Media-playlist tags we manage ourselves (everything else is passed through).
_MANAGED_MEDIA_TAGS = (
    "#EXTM3U", "#EXT-X-VERSION:", "#EXT-X-TARGETDURATION:",
    "#EXT-X-MEDIA-SEQUENCE:", "#EXT-X-DISCONTINUITY-SEQUENCE:",
    "#EXT-X-ENDLIST",
)


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
    attributes: dict
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
class MasterPlaylist:
    other_lines: list = field(default_factory=list)  # verbatim non-variant lines
    variants: list = field(default_factory=list)     # list[MasterVariant]


@dataclass
class MediaSegment:
    uri: str
    duration: float
    seq: int
    pdt: Optional[str] = None
    discontinuity_before: bool = False
    tags: list = field(default_factory=list)   # verbatim per-segment passthrough tags


@dataclass
class MediaPlaylist:
    version: int = 3
    target_duration: int = 6
    media_sequence: int = 0
    discontinuity_sequence: int = 0
    segments: list = field(default_factory=list)
    endlist: bool = False
    header_extra: list = field(default_factory=list)  # verbatim playlist-level passthrough tags


def parse_attributes(line: str) -> dict:
    body = line.split(":", 1)[1] if ":" in line else ""
    return {m.group(1): m.group(2).strip('"') for m in _ATTR_RE.finditer(body)}


def is_master(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


# --- master ----------------------------------------------------------------

def parse_master(text: str, base_url: str) -> MasterPlaylist:
    """Parse a master playlist preserving every non-variant line. EXT-X-MEDIA
    URIs (audio/subtitle renditions) are rewritten to absolute origin URLs so
    players fetch them straight from the origin."""
    master = MasterPlaylist()
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.strip().startswith("#EXT-X-STREAM-INF"):
            attrs = parse_attributes(line)
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j < len(lines):
                uri = urljoin(base_url, lines[j].strip())
                master.variants.append(MasterVariant(attributes=attrs, uri=uri, inf_line=line.strip()))
            i = j + 1
        elif not line.strip():
            i += 1
        else:
            # Preserve verbatim. For EXT-X-MEDIA, absolutize its URI attribute.
            if line.strip().startswith("#EXT-X-MEDIA") and "URI=" in line:
                def _abs(m):
                    return 'URI="' + urljoin(base_url, m.group(1)) + '"'
                line = _URI_ATTR_RE.sub(_abs, line)
            master.other_lines.append(line.strip())
            i += 1
    return master


def render_master(master: MasterPlaylist, child_url_for) -> str:
    """``child_url_for(index, variant)`` returns the child-manifest URI."""
    out = list(master.other_lines)
    if not out or out[0] != "#EXTM3U":
        out.insert(0, "#EXTM3U")
    for idx, v in enumerate(master.variants):
        out.append(v.inf_line)
        out.append(child_url_for(idx, v))
    return "\n".join(out) + "\n"


# --- media -----------------------------------------------------------------

def parse_media(text: str, base_url: str) -> MediaPlaylist:
    pl = MediaPlaylist()
    lines = text.splitlines()
    seq = 0
    pending_duration = 0.0
    pending_pdt: Optional[str] = None
    pending_disc = False
    pending_tags: list = []
    seen_first_segment = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line == "#EXTM3U":
            continue
        elif line.startswith("#EXT-X-VERSION:"):
            pl.version = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            pl.target_duration = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            pl.media_sequence = int(line.split(":", 1)[1])
            seq = pl.media_sequence
        elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            pl.discontinuity_sequence = int(line.split(":", 1)[1])
        elif line == "#EXT-X-DISCONTINUITY":
            pending_disc = True
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pending_pdt = line.split(":", 1)[1]
        elif line.startswith("#EXTINF:"):
            dur = line.split(":", 1)[1].split(",")[0]
            try:
                pending_duration = float(dur)
            except ValueError:
                pending_duration = 0.0
        elif line == "#EXT-X-ENDLIST":
            pl.endlist = True
        elif line.startswith("#"):
            # Any other tag -> passthrough. Before the first segment it's a
            # playlist-level tag; otherwise it belongs to the next segment.
            (pl.header_extra if not seen_first_segment else pending_tags).append(line)
        else:
            seen_first_segment = True
            pl.segments.append(MediaSegment(
                uri=urljoin(base_url, line),
                duration=pending_duration,
                seq=seq,
                pdt=pending_pdt,
                discontinuity_before=pending_disc,
                tags=pending_tags,
            ))
            seq += 1
            pending_duration = 0.0
            pending_pdt = None
            pending_disc = False
            pending_tags = []

    _forward_fill_pdt(pl)
    return pl


def _forward_fill_pdt(pl: MediaPlaylist) -> None:
    """Give every segment an effective PROGRAM-DATE-TIME by accumulating EXTINF
    durations from the last explicit anchor (many origins only stamp the first
    segment)."""
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
    out = ["#EXTM3U", f"#EXT-X-VERSION:{pl.version}",
           f"#EXT-X-TARGETDURATION:{pl.target_duration}",
           f"#EXT-X-MEDIA-SEQUENCE:{pl.media_sequence}",
           f"#EXT-X-DISCONTINUITY-SEQUENCE:{pl.discontinuity_sequence}"]
    out.extend(pl.header_extra)
    for seg in pl.segments:
        if seg.discontinuity_before:
            out.append("#EXT-X-DISCONTINUITY")
        out.extend(seg.tags)  # verbatim SCTE/CUE/etc.
        if seg.pdt:
            out.append(f"#EXT-X-PROGRAM-DATE-TIME:{seg.pdt}")
        out.append(f"#EXTINF:{seg.duration:.6f},")
        out.append(seg.uri)
    if pl.endlist:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"
