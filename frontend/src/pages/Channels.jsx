import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import ApiExplorer from "../ApiExplorer.jsx";

const DEFAULT_ORIGIN = "http://127.0.0.1:8100/master.m3u8";

const TABS = [
  ["register", "Register channel", "＋"],
  ["channels", "Channels", "☰"],
  ["api", "API explorer", "{ }"],
];

export default function Channels() {
  const [tab, setTab] = useState("register");
  const [channels, setChannels] = useState([]);
  const [masterUrl, setMasterUrl] = useState(DEFAULT_ORIGIN);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  async function refresh() {
    try { setChannels(await api.listChannels()); } catch { /* */ }
  }
  useEffect(() => { refresh(); }, []);

  async function register() {
    setErr("");
    try {
      setBusy(true);
      const ch = await api.ingest(masterUrl, name || "channel");
      nav(`/channel/${ch.id}`);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }
  async function del(id) { await api.raw("DELETE", `/api/channels/${id}`).catch(() => {}); refresh(); }

  return (
    <div className="tabs-layout">
      <aside className="tabs-side">
        {TABS.map(([k, label, icon]) => (
          <button key={k} className={`tab-btn ${tab === k ? "active" : ""}`}
                  onClick={() => { setTab(k); if (k === "channels") refresh(); }}>
            <span className="tab-ic">{icon}</span>{label}
          </button>
        ))}
      </aside>

      <section className="tabs-content">
        {tab === "register" && (
          <div className="card narrow-card">
            <h3>Register a channel</h3>
            <label className="field"><span>Channel name</span>
              <input value={name} onChange={(e) => setName(e.target.value)}
                     placeholder="e.g. Samsung Wildlife" /></label>
            <label className="field"><span>HLS master manifest URL (origin)</span>
              <input value={masterUrl} onChange={(e) => setMasterUrl(e.target.value)}
                     placeholder="https://cdn…/stream.m3u8" /></label>
            <button onClick={register} disabled={busy} style={{ width: "100%" }}>
              {busy ? "Registering…" : "Register & open console"}
            </button>
            {err && <p className="error">{err}</p>}
            <p className="hint" style={{ marginTop: 10 }}>
              The channel is saved to the database and stays in your list even
              after you stop ingestion.
            </p>
          </div>
        )}

        {tab === "channels" && (
          <div className="card">
            <div className="card-head"><h3>Your channels</h3>
              <button className="ghost small" onClick={refresh}>Refresh</button></div>
            {channels.length === 0 && <p className="hint">No channels yet — use “Register channel”.</p>}
            <ul className="channel-list">
              {channels.map((c) => (
                <li key={c.id}>
                  <span className={`status-dot ${c.status || "active"}`} />
                  <div className="ch-main">
                    <Link className="ch-name" to={`/channel/${c.id}`}>{c.name}</Link>
                    <span className="ch-url">{c.master_url}</span>
                  </div>
                  <span className={`chip ${c.status === "stopped" ? "expired" : "active"}`}>
                    {c.status === "stopped" ? "Stopped" : "Active"}</span>
                  <span className="badge">{c.variants?.length || 0} variants</span>
                  <Link className="ghost small" to={`/channel/${c.id}`}>Open</Link>
                  <button className="danger small" onClick={() => del(c.id)}>Delete</button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {tab === "api" && <ApiExplorer mode="common" />}
      </section>
    </div>
  );
}
