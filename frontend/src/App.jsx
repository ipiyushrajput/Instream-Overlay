import React, { useEffect, useRef, useState } from "react";
import { api, API_BASE } from "./api.js";
import Player from "./Player.jsx";
import OverlayControls from "./OverlayControls.jsx";

const DEFAULT_ORIGIN = "http://127.0.0.1:8100/master.m3u8";

export default function App() {
  const [masterUrl, setMasterUrl] = useState(DEFAULT_ORIGIN);
  const [channel, setChannel] = useState(null);
  const [overlays, setOverlays] = useState([]);
  const [status, setStatus] = useState(null);
  const [log, setLog] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const wsRef = useRef(null);

  function pushLog(line) {
    setLog((l) => [`${new Date().toLocaleTimeString()}  ${line}`, ...l].slice(0, 200));
  }

  async function ingest() {
    setErr("");
    try {
      setBusy(true);
      const ch = await api.ingest(masterUrl, "channel");
      setChannel(ch);
      pushLog(`Ingested ${ch.variants.length} variant(s) — channel ${ch.id}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function refreshOverlays() {
    if (!channel) return;
    try {
      setOverlays(await api.listOverlays(channel.id));
    } catch (e) { /* ignore */ }
  }

  // Poll live-edge/buffer status.
  useEffect(() => {
    if (!channel) return;
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.channelStatus(channel.id);
        if (alive) setStatus(s);
      } catch (e) { /* ignore */ }
    };
    tick();
    const id = setInterval(tick, 3000);
    refreshOverlays();
    const id2 = setInterval(refreshOverlays, 4000);
    return () => { alive = false; clearInterval(id); clearInterval(id2); };
  }, [channel]);

  // WebSocket for per-segment transcode status.
  useEffect(() => {
    const wsUrl = API_BASE.replace(/^http/, "ws") + "/ws";
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data);
        if (m.type === "segment_status") {
          pushLog(`seg v${m.variant_index} #${m.seq} → ${m.status}${m.error ? " (" + m.error + ")" : ""}`);
        } else if (m.type === "overlay_created") {
          pushLog(`overlay scheduled ${m.overlay.start_pdt} → ${m.overlay.end_pdt}`);
        } else if (m.type === "overlay_deleted") {
          pushLog(`overlay deleted ${m.overlay_id}`);
        }
      } catch (e) { /* ignore */ }
    };
    ws.onclose = () => pushLog("status websocket closed");
    return () => ws.close();
  }, []);

  return (
    <div className="app">
      <header>
        <h1>Instream Overlay — Operator Console</h1>
        <span className="sub">Transmit-style L-band / in-stream overlay injection</span>
      </header>

      <section className="ingest">
        <input
          value={masterUrl}
          onChange={(e) => setMasterUrl(e.target.value)}
          placeholder="HLS master manifest URL"
        />
        <button onClick={ingest} disabled={busy}>
          {busy ? "Ingesting…" : "Ingest"}
        </button>
        {channel && (
          <a className="outlink" href={api.masterUrl(channel.id)} target="_blank" rel="noreferrer">
            output master.m3u8 ↗
          </a>
        )}
      </section>
      {err && <p className="error">{err}</p>}

      <div className="grid">
        <div className="left">
          <div className="panel">
            <h3>Output preview (our stream)</h3>
            {channel ? <Player src={api.masterUrl(channel.id)} /> :
              <div className="placeholder">Ingest a stream to begin.</div>}
            {status && (
              <div className="status-bar">
                <span>live edge: <b>{status.live_edge_pdt || "—"}</b></span>
                <span>buffer: <b>{status.buffer_segments} seg</b></span>
                <span>window: <b>{status.segment_count} seg</b></span>
              </div>
            )}
          </div>

          <div className="panel">
            <h3>Active overlays</h3>
            {overlays.length === 0 && <p className="hint">None scheduled.</p>}
            <ul className="overlays">
              {overlays.map((o) => (
                <li key={o.id}>
                  <span className="tag">{o.overlay_type}</span>
                  <span className="times">{fmt(o.start_pdt)} → {fmt(o.end_pdt)}</span>
                  <button className="del" onClick={async () => { await api.deleteOverlay(o.id); refreshOverlays(); }}>✕</button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="right">
          {channel && <OverlayControls channelId={channel.id} onChange={refreshOverlays} />}
          <div className="panel log">
            <h3>Activity</h3>
            <pre>{log.join("\n")}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function fmt(iso) {
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso; }
}
