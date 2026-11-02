"""Which segments an overlay window covers, and the easing that rides on it.

Coverage is overlap-based rather than start-based: a 10s overlay has to cover
every segment it touches, otherwise it is cut short at one segment. The squeeze
easing and the art fade are driven off the same geometry, and they have to turn
off together — art left on top of un-squeezed video is the bug this pins.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.codecs import VideoParams, build_squeeze_filter
from app.manifest import MediaSegment
from app.models import OverlayEvent, OverlayType

BASE = datetime(2026, 7, 8, 11, 44, 0, tzinfo=timezone.utc)
VP = VideoParams(codec="h264", width=1280, height=720, fps=30, has_audio=True)


def seg(seq: int, start_offset: float, duration: float = 6.0):
    return MediaSegment(
        uri=f"seg_{seq}.ts", duration=duration, seq=seq,
        pdt=(BASE + timedelta(seconds=start_offset)).isoformat(), tags=[],
    )


def overlay(start: float, end: float):
    return OverlayEvent(
        id="ov", channel_id="ch", overlay_type=OverlayType.LBAND,
        image_filename="x.png",
        start_pdt=BASE + timedelta(seconds=start),
        end_pdt=BASE + timedelta(seconds=end),
    )


def covers(ov, s) -> bool:
    """The overlap rule used when serving: the segment's own interval must
    intersect the overlay window."""
    sp = datetime.fromisoformat(s.pdt)
    return sp < ov.end_pdt and sp + timedelta(seconds=s.duration) > ov.start_pdt


def test_a_window_covers_every_segment_it_touches():
    """A 10s overlay starting mid-segment spans two 6s segments. Counting only
    segments that *start* inside the window would find one, and the overlay
    would be cut short."""
    ov = overlay(8, 18)
    assert covers(ov, seg(1, 6)) is True    # 6-12, overlaps from 8
    assert covers(ov, seg(2, 12)) is True   # 12-18, overlaps until 18
    assert covers(ov, seg(0, 0)) is False   # 0-6, ends before it starts
    assert covers(ov, seg(3, 18)) is False  # starts exactly at the end


def test_boundaries_are_half_open():
    """A segment ending exactly as the overlay begins is not covered, and one
    beginning exactly at the end is not either — otherwise each overlay bleeds
    a segment at both ends."""
    ov = overlay(6, 12)
    assert covers(ov, seg(0, 0)) is False
    assert covers(ov, seg(1, 6)) is True
    assert covers(ov, seg(2, 12)) is False


@pytest.mark.parametrize("overlay_type", ["lband", "top_band", "bottom_band", "pip"])
def test_filter_graph_is_well_formed(overlay_type):
    f = build_squeeze_filter(VP, overlay_type, event_offset=0.0, duration=12.0,
                             seg_duration=6.0)
    assert "[outv]" in f
    assert f.count("[v]") >= 2 and f.count("[art]") >= 2  # every pad consumed
    assert "eval=frame" in f  # the pocket is animated, not static


def test_art_fades_in_on_the_first_segment_and_out_on_the_last():
    """The art has to appear and disappear with the squeeze. Only the segment
    holding each ramp carries the matching fade."""
    first = build_squeeze_filter(VP, "lband", event_offset=0.0, duration=12.0,
                                 seg_duration=6.0)
    last = build_squeeze_filter(VP, "lband", event_offset=6.0, duration=12.0,
                                seg_duration=6.0)
    middle = build_squeeze_filter(VP, "lband", event_offset=6.0, duration=24.0,
                                  seg_duration=6.0)

    assert "fade=t=in" in first
    assert "fade=t=out" in last
    assert "fade=" not in middle, "a held segment must not re-fade"


def test_leading_partial_segment_delays_the_squeeze_to_the_real_start():
    """When the overlay starts part-way into a segment, event_offset is
    negative and the ramp must begin at the true start, not at the segment
    boundary — otherwise the squeeze starts early."""
    f = build_squeeze_filter(VP, "lband", event_offset=-2.0, duration=12.0,
                             seg_duration=6.0)
    assert "fade=t=in:st=2.0000" in f  # 2s into this segment
    assert "(t+-2.0000)" in f          # easing uses event-global time


def test_hevc_and_h264_select_their_own_encoder():
    """The overlaid segment is spliced into the origin stream, so it has to be
    encoded in the origin's codec, not a fixed one."""
    from app.transcode import _video_encoder
    h264 = _video_encoder(VideoParams(codec="h264"), use_hw=False)
    hevc = _video_encoder(VideoParams(codec="hevc"), use_hw=False)
    assert "libx264" in h264 and "libx265" not in h264
    assert "libx265" in hevc
    assert "hvc1" in hevc, "HEVC must be hvc1-tagged to splice cleanly"
