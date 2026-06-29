import React, { useState } from "react";
import { api } from "./api.js";

// mode="common" -> common APIs (main page); mode="channel" -> channel-specific.
export default function ApiExplorer({ mode = "common", channelId }) {
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
    ["All Channel Status", () => api.health()],
    ["All Channel Details", () => api.listChannels()],
    ["Defaults", () => api.defaults()],
  ];
  const channel = channelId ? [
    ["Channel data", () => api.getChannel(channelId)],
    ["Channel Status", () => api.channelStatus(channelId)],
    ["Overlays", () => api.listOverlays(channelId)],
    ["Debug", () => api.channelDebug(channelId, 0)],
  ] : [];
  const buttons = mode === "channel" ? channel : common;

  return (
    <div className="card">
      <h3>API explorer</h3>
      <div className="api-btns">
        {buttons.map(([l, fn]) => (
          <button key={l} className="ghost small" onClick={() => call(l, fn)}>{l}</button>
        ))}
      </div>
      <div className="api-out">
        <div className="api-out-head">{busy ? "Loading…" : (title || "Pick an endpoint above")}</div>
        <pre>{out ? JSON.stringify(out, null, 2) : ""}</pre>
      </div>
    </div>
  );
}
