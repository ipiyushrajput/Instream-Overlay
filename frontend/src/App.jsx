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
      pushLog(`Ingested ${ch.variants.length} variant(s) — channel ${ch.id.slice(0, 8)}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function stopIngestion() {
    if (!channel) return;
    try {
      await api.stopChannel(channel.id);
      pushLog(`Stopped ingestion for ${channel.id.slice(0, 8)}`);
    } catch (e) { /* ignore */ }
    setChannel(null);
    setOverlays([]);
    setStatus(null);
  }

  async function refreshOverlays() {
    if (!channel) return;
    try { setOverlays(await api.listOverlays(channel.id)); } catch (e) { /* ignore */ }
  }

  // Poll live-edge/buffer status + overlays (gentle intervals; WS pushes events).
  useEffect(() => {
    if (!channel) return;
    let alive = true;
    const tickStatus = async () => {
      try { const s = await api.channelStatus(channel.id); if (alive) setStatus(s); }
      catch (e) { /* ignore */ }
    };
    tickStatus();
    refreshOverlays();
    const id1 = setInterval(tickStatus, 5000);
    const id2 = setInterval(refreshOverlays, 6000);
    return () => { alive = false; clearInterval(id1); clearInterval(id2); };
  }, [channel]);

  // WebSocket for live events (transcode status, overlay created/expired).
  useEffect(() => {
    const ws = new WebSocket(API_BASE.replace(/^http/, "ws") + "/ws");
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data);
        if (m.type === "segment_status" && m.status !== "processing") {
          pushLog(`seg v${m.variant_index} #${m.seq} → ${m.status}${m.error ? " (" + m.error.slice(0, 80) + ")" : ""}`);
        } else if (m.type === "overlay_created") {
          pushLog(`overlay scheduled`);
          refreshOverlays();
        } else if (m.type === "overlay_deleted" || m.type === "channel_stopped") {
          refreshOverlays();
        }
      } catch (e) { /* ignore */ }
    };
    ws.onclose = () => pushLog("status websocket closed");
    return () => ws.close();
  }, []); // eslint-disable-line

  const liveOn = !!status?.live_edge_pdt;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo">▶</div>
          <div>
            <h1>Instream Overlay</h1>
            <div className="sub">L-band &amp; in-stream overlay injection for live HLS</div>
          </div>
        </div>
        <div className={`live-pill ${liveOn ? "on" : ""}`}>
          <span className="dot" /> {liveOn ? "LIVE" : "idle"}
        </div>
      </header>

      <div className="ingest">
        <input
          value={masterUrl}
          onChange={(e) => setMasterUrl(e.target.value)}
          placeholder="HLS master manifest URL (e.g. https://cdn…/stream.m3u8)"
          disabled={!!channel}
        />
        {!channel ? (
          <button onClick={ingest} disabled={busy}>{busy ? "Ingesting…" : "Ingest"}</button>
        ) : (
          <button className="danger" onClick={stopIngestion}>Stop ingestion</button>
        )}
      </div>
      {err && <p className="error">{err}</p>}

      <div className="grid">
        <div className="left">
          <div className="card">
            <div className="card-head">
              <h3>Output preview</h3>
              {channel && (
                <a className="hint" href={api.masterUrl(channel.id)} target="_blank" rel="noreferrer">
                  open output .m3u8 ↗
                </a>
              )}
            </div>
            {channel ? <Player src={api.masterUrl(channel.id)} /> :
              <div className="placeholder">Ingest a stream to begin.</div>}
            {status && (
              <div className="stats">
                <div className="stat"><div className="k">Live edge</div>
                  <div className="v">{fmtTime(status.live_edge_pdt)}</div></div>
                <div className="stat"><div className="k">Buffer</div>
                  <div className="v">{status.buffer_segments} seg</div></div>
                <div className="stat"><div className="k">Min lead</div>
                  <div className="v">{status.min_lead_seconds}s</div></div>
                <div className="stat"><div className="k">Window</div>
                  <div className="v">{status.segment_count} seg</div></div>
                <div className="stat"><div className="k">Active</div>
                  <div className="v">{status.active_overlays}</div></div>
              </div>
            )}
          </div>

          <div className="card">
            <h3>Scheduled overlays</h3>
            {overlays.length === 0 && <p className="hint">None yet. Add one on the right →</p>}
            <ul className="overlays">
              {overlays.map((o) => (
                <li key={o.id}>
                  <span className={`chip ${o.status}`}>{o.status}</span>
                  <span className="ov-type">{o.overlay_type.replace("_", " ")}</span>
                  <span className="ov-times">{fmtTime(o.start_pdt)} → {fmtTime(o.end_pdt)}</span>
                  {o.injected_count > 0 && <span className="ov-inj">{o.injected_count} seg</span>}
                  <button className="del" title="delete"
                          onClick={async () => { await api.deleteOverlay(o.id); refreshOverlays(); }}>✕</button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="right">
          {channel ? (
            <OverlayControls channelId={channel.id} minLead={status?.min_lead_seconds || 12}
                             onChange={refreshOverlays} />
          ) : (
            <div className="card"><p className="hint">Overlay controls appear once a stream is ingested.</p></div>
          )}
          <div className="card log">
            <h3>Activity</h3>
            <pre>{log.join("\n") || "—"}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function fmtTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso; }
}
