import React, { useEffect, useRef, useState } from "react";

// "DVR scrubber with live window". Tracks the REAL viewer playhead
// (hls.playingDate from the output player), shows the latency band up to the
// LIVE edge, and places each overlay as a block with a live countdown
// ("appears in 0:08" -> "ON AIR 0:12 left"). Smooth via requestAnimationFrame,
// interpolating between the (sparse) clock/status updates at 1x wall-time.
function parse(d) {
  if (d == null) return null;
  const t = d instanceof Date ? d.getTime() : Date.parse(d);
  return Number.isNaN(t) ? null : t;
}
function fmtClock(ms) { return ms == null ? "—" : new Date(ms).toLocaleTimeString(); }
function fmtDur(s) {
  s = Math.max(0, Math.round(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
const clampPos = (p) => Math.max(0, Math.min(100, p));

export default function Timeline({ playheadPdt, liveEdgePdt, bufferSeconds = 0, overlays = [] }) {
  const phRef = useRef({ val: null, at: 0 });
  const liveRef = useRef({ val: null, at: 0 });
  const [, setTick] = useState(0);

  useEffect(() => {
    const v = parse(playheadPdt);
    if (v != null) phRef.current = { val: v, at: performance.now() };
  }, [playheadPdt]);
  useEffect(() => {
    const v = parse(liveEdgePdt);
    if (v != null) liveRef.current = { val: v, at: performance.now() };
  }, [liveEdgePdt]);

  useEffect(() => {
    let raf;
    const loop = () => { setTick((t) => (t + 1) % 1e6); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const perf = performance.now();
  const liveNow = liveRef.current.val != null ? liveRef.current.val + (perf - liveRef.current.at) : null;
  let phNow = phRef.current.val != null ? phRef.current.val + (perf - phRef.current.at) : null;
  let estimated = false;
  if (phNow == null && liveNow != null) { phNow = liveNow - (bufferSeconds + 6) * 1000; estimated = true; }

  if (phNow == null && liveNow == null) {
    return <div className="card"><h3>Live timeline</h3>
      <p className="hint">Waiting for the output player to start…</p></div>;
  }

  const ovs = overlays.map((o) => ({ ...o, s: parse(o.start_pdt), e: parse(o.end_pdt) }))
    .filter((o) => o.s != null && o.e != null);
  const upcoming = ovs.filter((o) => o.e > phNow - 4000).sort((a, b) => a.s - b.s);
  const next = upcoming.find((o) => phNow >= o.s && phNow < o.e) || upcoming.find((o) => o.s > phNow) || null;

  const right = Math.max(liveNow ?? phNow, ...upcoming.map((o) => o.e), phNow) + 6000;
  const left = phNow - 12000;
  const span = Math.max(30000, right - left);
  const pos = (t) => clampPos(((t - left) / span) * 100);
  const lbl = (t) => Math.max(5, Math.min(95, pos(t)));

  const behindLive = liveNow != null ? Math.max(0, (liveNow - phNow) / 1000) : null;
  let cd = null;
  if (next) {
    if (phNow < next.s) cd = { kind: "upcoming", secs: (next.s - phNow) / 1000 };
    else if (phNow < next.e) cd = { kind: "onair", secs: (next.e - phNow) / 1000 };
  }

  return (
    <div className="card timeline">
      <div className="card-head">
        <h3>Live timeline</h3>
        <div className="tl-meta">
          {behindLive != null && <span>behind live <b>{fmtDur(behindLive)}</b></span>}
          {estimated && <span className="est">· estimated playhead</span>}
        </div>
      </div>

      <div className="tl-track">
        <div className="tl-rail" />
        <div className="tl-played" style={{ left: 0, width: `${pos(phNow)}%` }} />
        {liveNow != null && liveNow > phNow &&
          <div className="tl-latency" style={{ left: `${pos(phNow)}%`, width: `${pos(liveNow) - pos(phNow)}%` }} />}
        {upcoming.map((o) => {
          const active = phNow >= o.s && phNow < o.e;
          return (
            <div key={o.id} className={`tl-ev ${active ? "on" : ""}`}
                 style={{ left: `${pos(o.s)}%`, width: `${Math.max(2, pos(o.e) - pos(o.s))}%` }}>
              <span>{o.overlay_type.replace("_", " ")}</span>
            </div>
          );
        })}
        {liveNow != null &&
          <div className="tl-live" style={{ left: `${pos(liveNow)}%` }}>
            <span className="tl-live-lbl">● LIVE</span>
          </div>}
        <div className="tl-play" style={{ left: `${pos(phNow)}%` }} />
      </div>

      <div className="tl-axis">
        <span className="tl-now mono" style={{ left: `${lbl(phNow)}%` }}>now {fmtClock(phNow)}</span>
        {next && cd &&
          <span className={`tl-cd ${cd.kind}`} style={{ left: `${lbl((next.s + next.e) / 2)}%` }}>
            {cd.kind === "upcoming"
              ? `${next.overlay_type.replace("_", " ")} appears in ${fmtDur(cd.secs)}`
              : `ON AIR · ${fmtDur(cd.secs)} left`}
          </span>}
      </div>

      {upcoming.length === 0 &&
        <p className="hint" style={{ marginTop: 8 }}>No overlays scheduled — add one to see it slide in here.</p>}
    </div>
  );
}
