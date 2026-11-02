"""The frozen-decision timeline.

HLS requires a published segment to keep its URI, duration and discontinuity
across reloads, and EXT-X-DISCONTINUITY-SEQUENCE to count the discontinuities
that have scrolled out of the window. These are the rules that produced the
subtlest bugs in this service — an off-by-one here is a player stall, not a
visible error — so they are pinned here.
"""
from datetime import datetime, timedelta, timezone

from app.manifest import MediaSegment
from app.timeline import VariantTimeline

BASE = datetime(2026, 7, 8, 11, 44, 0, tzinfo=timezone.utc)


def segs(first: int, n: int, duration: float = 6.0):
    return [
        MediaSegment(
            uri=f"seg_{first + i}.ts",
            duration=duration,
            seq=first + i,
            pdt=(BASE + timedelta(seconds=duration * i)).isoformat(),
            tags=[],
        )
        for i in range(n)
    ]


def origin_only(seg):
    return ("origin", seg.uri, None)


def overlay_for(overlaid: set):
    def decide(seg):
        if seg.seq in overlaid:
            return ("overlay", f"/segment/{seg.seq}.ts", "ov")
        return ("origin", seg.uri, None)
    return decide


def disc_at(playlist):
    return [s.seq for s in playlist.segments if s.discontinuity_before]


def test_holds_back_by_the_buffer():
    """The live edge is held `buffer` segments behind the origin's, which is the
    headroom the transcoder runs in."""
    tl = VariantTimeline()
    tl.advance(segs(100, 10), buffer_segments=3, decide=origin_only, window_size=10)
    assert tl.max_frozen_seq == 106  # origin last 109, minus 3


def test_decisions_are_frozen_across_reloads():
    """A segment published as origin must not become an overlay later: the
    player has already fetched that playlist."""
    tl = VariantTimeline()
    tl.advance(segs(100, 10), 3, origin_only, 10)
    before = {s.seq: s.uri for s in tl.render(0, 6).segments}

    # The same segments come back, now claiming to be overlaid.
    tl.advance(segs(100, 10), 3, overlay_for({101, 102}), 10)
    after = {s.seq: s.uri for s in tl.render(0, 6).segments}

    assert after[101] == before[101], "a frozen decision was rewritten"
    assert after[102] == before[102]


def test_discontinuity_wraps_the_overlay_run():
    """One discontinuity entering the overlay and one leaving it — and none
    between consecutive overlay segments, which share a continuous timeline."""
    tl = VariantTimeline()
    tl.advance(segs(100, 16), 3, overlay_for({106, 107, 108}), window_size=16)
    out = tl.render(0, 6)
    assert disc_at(out) == [106, 109]


def test_disc_sequence_counts_only_what_scrolled_out():
    """EXT-X-DISCONTINUITY-SEQUENCE counts discontinuities that have left the
    window. While the overlay is still visible nothing has scrolled, so it
    stays at the origin's own base."""
    tl = VariantTimeline()
    tl.advance(segs(100, 16), 3, overlay_for({106, 107, 108}), window_size=16)
    assert tl.render(0, 6).discontinuity_sequence == 0

    # Roll far enough that both injected discontinuities fall out of a short
    # window: the sequence must then count both.
    tl.advance(segs(100, 30), 3, overlay_for({106, 107, 108}), window_size=4)
    assert tl.render(0, 6).discontinuity_sequence == 2


def test_disc_sequence_adds_the_origin_base():
    """The origin's own discontinuity-sequence is carried, not replaced."""
    tl = VariantTimeline()
    tl.advance(segs(100, 16), 3, origin_only, 16)
    assert tl.render(7, 6).discontinuity_sequence == 7


def test_renditions_reuse_the_structure_with_their_own_timing():
    """A rendition renders through the video timeline, so discontinuities land
    on the same sequence numbers, while keeping its own URLs, PDT and
    durations. This is what keeps subtitles aligned with video."""
    tl = VariantTimeline()
    tl.advance(segs(100, 16), 3, overlay_for({106, 107, 108}), window_size=16)
    video = tl.render(0, 6)
    sub = tl.render(
        0, 6,
        uri_for=lambda q: f"en-vtt/seg_{q}.vtt",
        pdt_for=lambda q: f"pdt-{q}",
        dur_for=lambda q: 6.006,
    )
    assert disc_at(sub) == disc_at(video)
    assert sub.media_sequence == video.media_sequence
    assert sub.discontinuity_sequence == video.discontinuity_sequence
    assert all(s.uri.startswith("en-vtt/") for s in sub.segments)
    assert all(s.duration == 6.006 for s in sub.segments)


def test_waiting_decision_stops_the_edge_rather_than_guessing():
    """decide() returning None means 'not ready'. The edge must stop there
    instead of publishing a segment it would want to replace."""
    tl = VariantTimeline()

    def stall_at_103(seg):
        return None if seg.seq == 103 else ("origin", seg.uri, None)

    tl.advance(segs(100, 10), 3, stall_at_103, 10)
    assert tl.max_frozen_seq == 102


def test_window_size_bounds_the_published_history():
    tl = VariantTimeline()
    tl.advance(segs(100, 30), 3, origin_only, window_size=6)
    assert len(tl.render(0, 6).segments) == 6
