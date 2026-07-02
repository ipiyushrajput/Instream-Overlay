import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

const TYPE_COLOR = { lband: "#2dd4a7", top_band: "#7c3aed", bottom_band: "#059669", pip: "#e5484d" };

function fmt(iso) { if (!iso) return "—"; try { return new Date(iso).toLocaleString(); } catch { return iso; } }

export default function Insights() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try { setRows(await api.insights()); } catch { /* */ } finally { setLoading(false); }
  }
  useEffect(() => { refresh(); const i = setInterval(refresh, 6000); return () => clearInterval(i); }, []);

  const totals = rows.reduce((a, r) => ({
    sessions: a.sessions + (r.sessions?.total || 0),
    delivered: a.delivered + (r.overlays?.delivered || 0),
    segments: a.segments + (r.overlays?.total_segments || 0),
  }), { sessions: 0, delivered: 0, segments: 0 });

  return (
    <div>
      <div className="page-head">
        <h2 className="page-title" style={{ margin: 0 }}>Insights</h2>
        <button className="ghost small" onClick={refresh}>Refresh</button>
      </div>

      <div className="kpi-row">
        <div className="kpi"><div className="kpi-k">Channels</div><div className="kpi-v">{rows.length}</div></div>
        <div className="kpi"><div className="kpi-k">Viewer sessions</div><div className="kpi-v">{totals.sessions}</div></div>
        <div className="kpi"><div className="kpi-k">Overlays delivered</div><div className="kpi-v">{totals.delivered}</div></div>
        <div className="kpi"><div className="kpi-k">Overlay segments on air</div><div className="kpi-v">{totals.segments}</div></div>
      </div>

      {loading && <p className="hint">Loading…</p>}
      {!loading && rows.length === 0 && <p className="hint">No channels yet — register one to start collecting insights.</p>}

      {rows.map((r) => {
        const types = Object.entries(r.overlays?.by_type || {});
        return (
          <div className="card ins-card" key={r.channel_id}>
            <div className="ins-head">
              <div>
                <Link className="ch-name" to={`/channel/${r.channel_id}`}>{r.name}</Link>
                <span className="hint" style={{ marginLeft: 10 }}>{r.codec?.toUpperCase()}</span>
              </div>
              <span className={`chip ${r.status === "stopped" ? "expired" : "active"}`}>
                {r.status === "stopped" ? "Stopped" : "Active"}</span>
            </div>

            <div className="ins-grid">
              <div className="ins-stat"><div className="k">Sessions</div><div className="v">{r.sessions?.total || 0}</div></div>
              <div className="ins-stat"><div className="k">First seen</div><div className="v sm">{fmt(r.sessions?.first)}</div></div>
              <div className="ins-stat"><div className="k">Last seen</div><div className="v sm">{fmt(r.sessions?.last)}</div></div>
              <div className="ins-stat"><div className="k">Scheduled</div><div className="v">{r.overlays?.scheduled || 0}</div></div>
              <div className="ins-stat"><div className="k">Delivered</div><div className="v">{r.overlays?.delivered || 0}</div></div>
              <div className="ins-stat"><div className="k">Completed</div><div className="v">{r.overlays?.completed || 0}</div></div>
              <div className="ins-stat"><div className="k">Segments on air</div><div className="v">{r.overlays?.total_segments || 0}</div></div>
            </div>

            {types.length > 0 && (
              <div className="ins-types">
                <span className="hint">By overlay type:</span>
                {types.map(([t, e]) => (
                  <span className="type-pill" key={t}>
                    <i style={{ background: TYPE_COLOR[t] || "#888" }} />
                    {t.replace("_", " ")}: <b>{e.delivered}</b> delivered · {e.segments} seg
                  </span>
                ))}
              </div>
            )}

            {r.sessions?.recent?.length > 0 && (
              <details className="ins-sessions">
                <summary>{r.sessions.recent.length} recent session(s)</summary>
                <table>
                  <thead><tr><th>Started</th><th>Session (uid)</th><th>IP</th><th>User agent</th></tr></thead>
                  <tbody>
                    {r.sessions.recent.map((s) => (
                      <tr key={s.uid}>
                        <td className="sm">{fmt(s.session_start)}</td>
                        <td className="mono sm">{s.uid?.slice(0, 12)}</td>
                        <td className="sm">{s.remote_ip}</td>
                        <td className="sm ellipsis">{s.user_agent}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}
