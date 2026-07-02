import React, { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, API_BASE } from "../api.js";
import Player from "../Player.jsx";
import OverlayControls from "../OverlayControls.jsx";
import ApiExplorer from "../ApiExplorer.jsx";
import Timeline from "../Timeline.jsx";

export default function ChannelConsole() {
  const { id } = useParams();
  const nav = useNavigate();
  const [channel, setChannel] = useState(null);
  const [status, setStatus] = useState(null);
  const [overlays, setOverlays] = useState([]);
  const [playheadPdt, setPlayheadPdt] = useState(null);
  const [copied, setCopied] = useState(false);
  const [log, setLog] = useState([]);
  const wsRef = useRef(null);

  const outputUrl = api.masterUrl(id);

  async function copyUrl() {
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(outputUrl);
        ok = true;
      }
    } catch { /* fall through to legacy path */ }
    if (!ok) {
      // Fallback for non-secure contexts / older browsers.
      const ta = document.createElement("textarea");
      ta.value = outputUrl;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus(); ta.select();
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      document.body.removeChild(ta);
    }
    setCopied(ok);
    setTimeout(() => setCopied(false), 1500);
  }

  function pushLog(line) {
    setLog((l) => [`${new Date().toLocaleTimeString()}  ${line}`, ...l].slice(0, 150));
  }
  async function refreshOverlays() {
    try { setOverlays(await api.listOverlays(id)); } catch { /* ignore */ }
  }

  useEffect(() => {
    let alive = true;
    api.getChannel(id).then((c) => alive && setChannel(c)).catch(() => nav("/"));
    const tick = async () => {
      try { const s = await api.channelStatus(id); if (alive) setStatus(s); } catch { /* */ }
    };
    tick(); refreshOverlays();
    const i1 = setInterval(tick, 5000);
    const i2 = setInterval(refreshOverlays, 6000);
    return () => { alive = false; clearInterval(i1); clearInterval(i2); };
  }, [id]);

  useEffect(() => {
    const ws = new WebSocket(API_BASE.replace(/^http/, "ws") + "/ws");
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data);
        if (m.channel_id && m.channel_id !== id) return;
        if (m.type === "segment_status" && m.status !== "processing")
          pushLog(`seg v${m.variant_index} #${m.seq} → ${m.status}`);
        else if (m.type === "overlay_created") { pushLog("overlay scheduled"); refreshOverlays(); }
        else if (m.type === "overlay_deleted") refreshOverlays();
      } catch { /* */ }
    };
    return () => ws.close();
  }, [id]);

  async function stop() {
    const ch = await api.stopChannel(id).catch(() => null);
    if (ch) setChannel(ch);
  }
  async function start() {
    const ch = await api.startChannel(id).catch(() => null);
    if (ch) setChannel(ch);
  }
  async function del() {
    await api.deleteChannel(id).catch(() => {});
    nav("/");
  }

  if (!channel) return <div className="card">Loading…</div>;
  const stopped = channel.status === "stopped";
  const liveOn = !stopped && !!status?.live_edge_pdt;

  return (
    <div>
      <div className="console-head">
        <div>
          <h2 style={{ margin: 0 }}>{channel.name}</h2>
          <div className="hint">{channel.master_url}</div>
        </div>
        <div className="head-actions">
          <span className={`live-pill ${liveOn ? "on" : ""}`}>
            <span className="dot" />{stopped ? "STOPPED" : (liveOn ? "LIVE" : "idle")}</span>
          {stopped
            ? <button className="success" onClick={start}>Start ingestion</button>
            : <button className="ghost" onClick={stop}>Stop ingestion</button>}
          <button className="danger" onClick={del}>Delete</button>
        </div>
      </div>

      {/* Dual preview: input origin (left) vs our overlaid output (right) */}
      <div className="dual">
        <div className="card">
          <div className="card-head"><h3>Input (origin)</h3></div>
          <Player src={channel.master_url} />
          <div className="url-line"><code>{channel.master_url}</code></div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Output (overlaid)</h3>
            <a className="hint" href={outputUrl} target="_blank" rel="noreferrer">open ↗</a></div>
          <Player src={outputUrl} onError={() => {}} onClock={setPlayheadPdt} />
          <div className="url-line"><code>{outputUrl}</code>
            <button className="ghost small" onClick={copyUrl}>{copied ? "Copied ✓" : "Copy"}</button></div>
        </div>
      </div>

      <Timeline
        playheadPdt={playheadPdt}
        liveEdgePdt={status?.live_edge_pdt}
        bufferSeconds={(status?.buffer_segments || 0) * (status?.target_duration || 6)}
        overlays={overlays}
      />

      {status && (
        <div className="stats">
          <div className="stat"><div className="k">Live edge</div><div className="v">{fmt(status.live_edge_pdt)}</div></div>
          <div className="stat"><div className="k">Buffer</div><div className="v">{status.buffer_segments} seg</div></div>
          <div className="stat"><div className="k">Min lead</div><div className="v">{status.min_lead_seconds}s</div></div>
          <div className="stat"><div className="k">Window</div><div className="v">{status.segment_count} seg</div></div>
          <div className="stat"><div className="k">Active</div><div className="v">{status.active_overlays}</div></div>
        </div>
      )}

      <div className="grid">
        <div className="left">
          <div className="card">
            <h3>Scheduled overlays</h3>
            {overlays.length === 0 && <p className="hint">None yet — add one on the right →</p>}
            <ul className="overlays">
              {overlays.map((o) => (
                <li key={o.id}>
                  <span className={`chip ${o.status}`}>{o.status}</span>
                  <span className="ov-type">{o.overlay_type.replace("_", " ")}</span>
                  <span className="ov-times">{fmt(o.start_pdt)} → {fmt(o.end_pdt)}</span>
                  {o.injected_count > 0 && <span className="ov-inj">{o.injected_count} seg</span>}
                  <button className="del" onClick={async () => { await api.deleteOverlay(o.id); refreshOverlays(); }}>✕</button>
                </li>
              ))}
            </ul>
          </div>
          <ApiExplorer mode="channel" channelId={id} />
        </div>
        <div className="right">
          <OverlayControls channelId={id} minLead={status?.min_lead_seconds || 24}
                           onChange={refreshOverlays} onLog={pushLog} />
          <div className="card log"><h3>Activity</h3><pre>{log.join("\n") || "—"}</pre></div>
        </div>
      </div>
    </div>
  );
}

function fmt(iso) { if (!iso) return "—"; try { return new Date(iso).toLocaleTimeString(); } catch { return iso; } }
