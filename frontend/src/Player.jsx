import React, { useEffect, useRef } from "react";
import Hls from "hls.js";

// Plays OUR mirrored master manifest. Overlays are burned into the stream, so
// they simply appear in the video when an overlay window is active.
export default function Player({ src, onError }) {
  const videoRef = useRef(null);
  const hlsRef = useRef(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;

    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    if (Hls.isSupported()) {
      const hls = new Hls({
        liveSyncDurationCount: 3,
        liveMaxLatencyDurationCount: 12,
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 30,
      });
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));

      hls.on(Hls.Events.ERROR, (_evt, data) => {
        // Expose for diagnostics/tests.
        window.__hlsErrors = window.__hlsErrors || [];
        window.__hlsErrors.push({ type: data.type, details: data.details, fatal: data.fatal });
        onError?.(data);
        if (!data.fatal) return;
        // Recover from transient fatal errors instead of dying (important at
        // discontinuity splices and live-edge churn).
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            hls.startLoad();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            hls.recoverMediaError();
            break;
          default:
            hls.destroy();
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src; // Safari native HLS
      video.play().catch(() => {});
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [src]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <video ref={videoRef} controls muted playsInline
           style={{ width: "100%", background: "#0b0e16", borderRadius: 12 }} />
  );
}
