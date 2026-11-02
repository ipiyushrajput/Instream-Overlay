"""Manifest parsing and rendering.

We mirror a live origin, so anything we fail to carry through is silently
dropped from the stream the viewer receives — SCTE-35 cue markers above all.
These tests pin the round trip.
"""
from app import manifest

MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:205500
#EXT-X-DISCONTINUITY-SEQUENCE:2
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-PROGRAM-DATE-TIME:2026-07-08T11:44:29.995633+00:00
#EXTINF:6.006000,
seg_0205500.ts
#EXT-X-CUE-OUT:30.000
#EXT-X-PROGRAM-DATE-TIME:2026-07-08T11:44:36.001633+00:00
#EXTINF:6.006000,
seg_0205501.ts
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-07-08T11:44:42.007633+00:00
#EXTINF:5.972633,
seg_0205502.ts
"""

MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",URI="en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=3440800,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2"
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1460800,RESOLUTION=854x480,CODECS="avc1.64001f,mp4a.40.2"
480p/index.m3u8
"""


def test_media_playlist_header_is_read():
    pl = manifest.parse_media(MEDIA, "https://cdn.example/live/index.m3u8")
    assert pl.target_duration == 6
    assert pl.media_sequence == 205500
    assert pl.discontinuity_sequence == 2
    assert len(pl.segments) == 3


def test_sequence_numbers_follow_media_sequence():
    pl = manifest.parse_media(MEDIA, "https://cdn.example/live/index.m3u8")
    assert [s.seq for s in pl.segments] == [205500, 205501, 205502]


def test_segment_uris_are_absolutised():
    """We hand these URLs to a player on a different host, so a relative URI
    would resolve against us instead of the origin."""
    pl = manifest.parse_media(MEDIA, "https://cdn.example/live/index.m3u8")
    assert pl.segments[0].uri == "https://cdn.example/live/seg_0205500.ts"


def test_durations_and_pdt_are_preserved_exactly():
    pl = manifest.parse_media(MEDIA, "https://cdn.example/live/index.m3u8")
    assert pl.segments[0].duration == 6.006
    assert pl.segments[2].duration == 5.972633
    assert pl.segments[0].pdt.startswith("2026-07-08T11:44:29.995633")


def test_native_discontinuity_is_kept():
    pl = manifest.parse_media(MEDIA, "https://cdn.example/live/index.m3u8")
    assert pl.segments[2].discontinuity_before is True


def test_cue_markers_ride_with_their_segment():
    """SCTE-35 drives ad insertion downstream. Dropping a CUE tag, or attaching
    it to the wrong segment, breaks the thing the stream exists for."""
    pl = manifest.parse_media(MEDIA, "https://cdn.example/live/index.m3u8")
    assert any("CUE-OUT" in t for t in pl.segments[1].tags)
    assert not any("CUE-OUT" in t for t in pl.segments[0].tags)


def test_render_round_trips_the_essentials():
    pl = manifest.parse_media(MEDIA, "https://cdn.example/live/index.m3u8")
    out = manifest.render_media(pl)
    assert "#EXT-X-MEDIA-SEQUENCE:205500" in out
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:2" in out
    assert "#EXT-X-DISCONTINUITY" in out
    assert "#EXT-X-CUE-OUT:30.000" in out
    assert out.startswith("#EXTM3U")
    assert out.endswith("\n")


def test_crlf_origin_parses():
    """A manifest served with Windows line endings must parse the same."""
    pl = manifest.parse_media(MEDIA.replace("\n", "\r\n"),
                              "https://cdn.example/live/index.m3u8")
    assert len(pl.segments) == 3
    assert pl.segments[0].duration == 6.006


def test_master_variants_and_renditions():
    master = manifest.parse_master(MASTER, "https://cdn.example/master.m3u8")
    assert len(master.variants) == 2
    assert master.variants[0].uri == "https://cdn.example/720p/index.m3u8"
    assert master.variants[0].resolution == "1280x720"
    assert "avc1" in master.variants[0].codecs
    assert len(master.renditions) == 1


def test_is_master_distinguishes_the_two():
    assert manifest.is_master(MASTER) is True
    assert manifest.is_master(MEDIA) is False
