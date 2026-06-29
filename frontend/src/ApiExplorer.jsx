import React, { useState } from "react";
import { api } from "./api.js";

// Lets the operator call every endpoint and inspect the JSON. Common endpoints
// are always available; channel-specific ones are passed a channelId.
export default function ApiExplorer({ channelId }) {
  const [out, setOut] = useState(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  async function call(label, fn) {
    setBusy(true); setTitle(label);
    try { setOut(await fn()); }
    catch (e) { setOut({ error: String(e) }); }
    finally { setBusy(false); }
  }

  const common = [
    ["GET /api/health", () => api.health()],
    ["GET /api/channels", () => api.listChannels()],
    ["GET /api/defaults", () => api.defaults()],
  ];
  const channel = channelId ? [
    ["GET /api/channels/{id}", () => api.getChannel(channelId)],
    ["GET …/status", () => api.channelStatus(channelId)],
    ["GET …/overlays", () => api.listOverlays(channelId)],
    ["GET …/debug (v0)", () => api.channelDebug(channelId, 0)],
  ] : [];

  return (
    <div className="card">
      <h3>API explorer</h3>
      <div className="api-group"><span className="api-label">Common</span>
        <div className="api-btns">
          {common.map(([l, fn]) => (
            <button key={l} className="ghost small" onClick={() => call(l, fn)}>{l}</button>
          ))}
        </div>
      </div>
      {channelId && (
        <div className="api-group"><span className="api-label">This channel</span>
          <div className="api-btns">
            {channel.map(([l, fn]) => (
              <button key={l} className="ghost small" onClick={() => call(l, fn)}>{l}</button>
            ))}
          </div>
        </div>
      )}
      {title && (
        <div className="api-out">
          <div className="api-out-head">{busy ? "Loading…" : title}</div>
          <pre>{out ? JSON.stringify(out, null, 2) : ""}</pre>
        </div>
      )}
    </div>
  );
}
