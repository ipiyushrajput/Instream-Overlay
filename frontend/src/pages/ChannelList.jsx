import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function ChannelList() {
  const [channels, setChannels] = useState([]);
  async function refresh() { try { setChannels(await api.listChannels()); } catch { /* */ } }
  useEffect(() => { refresh(); }, []);
  async function del(id) { await api.deleteChannel(id).catch(() => {}); refresh(); }

  return (
    <div>
      <h2 className="page-title">Channels</h2>
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
              <Link className="success small btn" to={`/channel/${c.id}`}>Open</Link>
              <button className="danger small" onClick={() => del(c.id)}>Delete</button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
