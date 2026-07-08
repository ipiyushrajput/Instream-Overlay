#!/usr/bin/env python3
"""Local multi-variant LIVE HLS origin simulator.

Stands in for the real CloudFront origin (which is blocked by this environment's
egress policy) so the whole overlay pipeline is testable end-to-end here.

It runs ffmpeg to produce a 3-variant live HLS (720p/480p/360p) with
EXT-X-PROGRAM-DATE-TIME, then serves the output directory over HTTP with CORS.

    python tools/gen_origin.py --port 8100 --dir /tmp/origin

Master playlist: http://107.109.131.68:8100/master.m3u8
"""
from __future__ import annotations

import argparse
import functools
import http.server
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def build_ffmpeg_cmd(out: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-re",
        # Synthetic video with a moving pattern + timestamp, and a tone.
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000",
        "-map", "0:v", "-map", "1:a",
        "-map", "0:v", "-map", "1:a",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "3.1",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
        "-s:v:0", "1280x720", "-b:v:0", "3000k", "-maxrate:v:0", "3000k", "-bufsize:v:0", "6000k",
        "-s:v:1", "854x480", "-b:v:1", "1200k", "-maxrate:v:1", "1200k", "-bufsize:v:1", "2400k",
        "-s:v:2", "640x360", "-b:v:2", "700k", "-maxrate:v:2", "700k", "-bufsize:v:2", "1400k",
        "-f", "hls", "-hls_time", "6", "-hls_list_size", "6",
        "-hls_flags", "delete_segments+program_date_time+independent_segments",
        "-hls_segment_filename", str(out / "v%v" / "seg_%05d.ts"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", "v:0,a:0 v:1,a:1 v:2,a:2",
        str(out / "v%v" / "index.m3u8"),
    ]


class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):  # quieter
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--dir", default=str(Path(tempfile.gettempdir()) / "instream-origin"))
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH", file=sys.stderr)
        return 1

    out = Path(args.dir)
    if out.exists():
        shutil.rmtree(out)
    for v in ("v0", "v1", "v2"):
        (out / v).mkdir(parents=True, exist_ok=True)

    ff = subprocess.Popen(build_ffmpeg_cmd(out))

    handler = functools.partial(CORSHandler, directory=str(out))
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"Origin serving http://107.109.131.68:{args.port}/master.m3u8 (dir={out})")

    def shutdown(*_):
        ff.terminate()
        httpd.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ff.wait()
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
