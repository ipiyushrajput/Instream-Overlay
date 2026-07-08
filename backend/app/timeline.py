"""Per-(channel, variant) playlist timeline with frozen decisions.

The HLS live-playlist contract is: once a segment is published at a given Media
Sequence Number, its URI / duration / discontinuity MUST NOT change on reload.
Our earlier builder re-decided every segment on every request, so a segment
could flip from origin -> overlaid (or gain/lose a discontinuity) between
reloads, which makes hls.js throw ``levelParsingError``.

This module fixes that by **freezing** each segment's decision the first time it
is exposed and reusing it forever after. It also computes
``EXT-X-DISCONTINUITY-SEQUENCE`` correctly as discontinuities scroll out of the
window, per RFC 8216.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .manifest import MediaPlaylist, MediaSegment


@dataclass
class Decision:
    seq: int
    uri: str                      # final URI written to the manifest
    duration: float
    pdt: Optional[str]
    kind: str                     # 'origin' | 'overlay'
    overlay_id: Optional[str]
    disc_native: bool             # origin had an EXT-X-DISCONTINUITY here
    disc_injected: bool           # origin<->overlay boundary we introduced
    tags: list = field(default_factory=list)  # verbatim per-segment passthrough tags


# decide() returns one of:
#   ("origin", uri, None)          -> serve origin segment
#   ("overlay", uri, overlay_id)   -> serve our transcoded segment (READY)
#   None                           -> covered but transcode not ready yet (wait)
DecideFn = Callable[[MediaSegment], Optional[tuple]]


@dataclass
class VariantTimeline:
    frozen: dict[int, Decision] = field(default_factory=dict)
    first_seen: dict[int, float] = field(default_factory=dict)
    scrolled_injected: int = 0     # injected discontinuities that have scrolled out
    max_frozen_seq: int = -1
    prev_kind: str = "origin"      # kind of the most recently frozen segment

    def _prune_below(self, low: int) -> None:
        for s in [s for s in self.frozen if s < low]:
            d = self.frozen.pop(s)
            self.first_seen.pop(s, None)
            if d.disc_injected:
                self.scrolled_injected += 1

    def advance(self, segments: list[MediaSegment], buffer_segments: int,
                decide: DecideFn, window_size: int, max_wait: float = 25.0) -> None:
        """Freeze new decisions up to ``origin_last - buffer_segments`` and keep
        the last ``window_size`` of them in history.

        Stops advancing at the first covered-but-not-ready segment (so the live
        edge waits for the transcode instead of publishing an origin segment we
        would later want to replace). After ``max_wait`` seconds we give up on
        that segment and freeze it as origin, so a failed transcode can't stall
        the edge forever.

        We retain ``window_size`` segments of history (not just origin's current
        window) so the output can mirror the origin's full window length even
        though our live edge is held ``buffer_segments`` behind it.
        """
        if not segments:
            return
        now = time.monotonic()
        by_seq = {s.seq: s for s in segments}
        origin_first = segments[0].seq
        origin_last = segments[-1].seq
        candidate_max = origin_last - max(0, buffer_segments)

        s = self.max_frozen_seq + 1 if self.max_frozen_seq >= 0 else origin_first
        if s < origin_first:
            # We fell behind; origin already dropped those segments.
            s = origin_first
        while s <= candidate_max:
            seg = by_seq.get(s)
            if seg is None:
                s += 1
                continue
            self.first_seen.setdefault(seg.seq, now)
            result = decide(seg)
            if result is None:
                if now - self.first_seen[seg.seq] < max_wait:
                    break  # wait for the transcode; don't advance past it
                result = ("origin", seg.uri, None)  # deadline -> drop overlay
            kind, uri, overlay_id = result
            self.frozen[seg.seq] = Decision(
                seq=seg.seq, uri=uri, duration=seg.duration, pdt=seg.pdt,
                kind=kind, overlay_id=overlay_id,
                disc_native=seg.discontinuity_before,
                disc_injected=(kind != self.prev_kind),
                tags=list(seg.tags))
            self.prev_kind = kind
            self.max_frozen_seq = seg.seq
            s += 1

        # Keep a full window of history behind the held-back edge.
        render_low = max(0, self.max_frozen_seq - max(1, window_size) + 1)
        self._prune_below(render_low)

    def render(self, origin_base_disc_seq: int, target_duration: int,
               version: int = 3, header_extra: Optional[list] = None,
               uri_for=None, tags_for=None, pdt_for=None, dur_for=None) -> MediaPlaylist:
        """Render the frozen window. ``uri_for(seq)`` / ``tags_for(seq)`` /
        ``pdt_for(seq)`` / ``dur_for(seq)`` let an alternate rendition
        (audio/subtitle) reuse this timeline's exact structure — same
        MEDIA-SEQUENCE, DISCONTINUITY-SEQUENCE and discontinuity positions — while
        substituting its OWN segment URLs, tags, PROGRAM-DATE-TIME and durations,
        so video and its renditions stay perfectly aligned on discontinuities yet
        each keeps its own timing. Any hook returning None falls back to this
        timeline's own value for that segment."""
        pl = MediaPlaylist(version=version, target_duration=target_duration)
        pl.header_extra = header_extra or []
        seqs = sorted(self.frozen)
        if not seqs:
            pl.media_sequence = max(0, self.max_frozen_seq + 1)
            return pl
        low = seqs[0]
        pl.discontinuity_sequence = (origin_base_disc_seq + self.scrolled_injected
                                     + (1 if self.frozen[low].disc_injected else 0))
        pl.media_sequence = low
        for i, sq in enumerate(seqs):
            d = self.frozen[sq]
            uri = uri_for(sq) if uri_for else d.uri
            if uri is None:
                # Rendition segment for this seq is unavailable — reuse the
                # decision's own tags but skip only if we truly have nothing.
                uri = d.uri
            tags = (tags_for(sq) if tags_for else d.tags) or []
            pdt = (pdt_for(sq) if pdt_for else None) or d.pdt
            dur = (dur_for(sq) if dur_for else None) or d.duration
            seg = MediaSegment(uri=uri, duration=dur, seq=d.seq,
                               pdt=pdt, tags=list(tags))
            seg.discontinuity_before = (i > 0) and (d.disc_native or d.disc_injected)
            pl.segments.append(seg)
        return pl

    def injected_overlay_seqs(self) -> dict[str, set]:
        out: dict[str, set] = {}
        for d in self.frozen.values():
            if d.kind == "overlay" and d.overlay_id:
                out.setdefault(d.overlay_id, set()).add(d.seq)
        return out
