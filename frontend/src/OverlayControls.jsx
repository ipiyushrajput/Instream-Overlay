import React, { useEffect, useRef, useState } from "react";
import { api, API_BASE } from "./api.js";

// Visual representation of where each overlay type sits in the frame.
const TYPES = [
  ["lband", "L-band", { bottom: 0, left: 0, right: 0, height: "30%" }],
  ["lower_third", "Lower ⅓", { bottom: "8%", left: "8%", width: "55%", height: "22%" }],
  ["top_banner", "Top", { top: 0, left: 0, right: 0, height: "26%" }],
  ["full_frame", "Full", { inset: 0 }],
  ["custom", "Custom", { top: "30%", left: "30%", width: "40%", height: "40%" }],
];

export default function OverlayControls({ channelId, minLead, onChange }) {
  const [image, setImage] = useState(null);
  const [type, setType] = useState("lband");
  const [startIn, setStartIn] = useState(minLead);
  const [duration, setDuration] = useState(30);
  const [xFrac, setXFrac] = useState(0.5);
  const [yFrac, setYFrac] = useState(0.85);
  const [scaleFrac, setScaleFrac] = useState(0.4);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef(null);

  // Keep the default start-in aligned with the server's required lead time.
  useEffect(() => { setStartIn((v) => (v < minLead ? minLead : v)); }, [minLead]);

  async function handleFile(file) {
    if (!file) return;
    setErr("");
    try { setBusy(true); setImage(await api.uploadImage(file)); }
    catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  async function addOverlay() {
    if (!channelId || !image) { setErr("Upload an overlay image first."); return; }
    setErr("");
    try {
      setBusy(true);
      await api.createRelative({
        channel_id: channelId,
        image_filename: image.image_filename,
        overlay_type: type,
        start_in_seconds: Number(startIn),
        duration_seconds: Number(duration),
        x_frac: Number(xFrac), y_frac: Number(yFrac), scale_frac: Number(scaleFrac),
      });
      onChange?.();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  const belowLead = Number(startIn) < minLead;

  return (
    <div className="card">
      <h3>Add overlay</h3>

      <div
        className="dropzone"
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); handleFile(e.dataTransfer.files?.[0]); }}
      >
        {image ? "Replace overlay image…" : "Click or drop a PNG (with transparency) here"}
        <input ref={fileRef} type="file" accept="image/*" hidden
               onChange={(e) => handleFile(e.target.files?.[0])} />
      </div>
      {image && (
        <div className="preview">
          <img src={`${API_BASE}${new URL(image.url).pathname}`} alt="overlay" />
          <code>{image.image_filename}</code>
        </div>
      )}

      <div style={{ height: 14 }} />
      <span className="hint" style={{ display: "block", marginBottom: 8 }}>Placement</span>
      <div className="type-grid">
        {TYPES.map(([v, label, box]) => (
          <div key={v} className={`type-opt ${type === v ? "active" : ""}`} onClick={() => setType(v)}>
            <span className="ic"><i style={box} /></span>
            {label}
          </div>
        ))}
      </div>

      <div className="row">
        <label className="field"><span>Start in (s)</span>
          <input type="number" min="0" value={startIn}
                 onChange={(e) => setStartIn(e.target.value)} /></label>
        <label className="field"><span>Duration (s)</span>
          <input type="number" min="1" value={duration}
                 onChange={(e) => setDuration(e.target.value)} /></label>
      </div>

      {type === "custom" && (
        <div className="row">
          <label className="field"><span>X</span>
            <input type="number" step="0.05" min="0" max="1" value={xFrac}
                   onChange={(e) => setXFrac(e.target.value)} /></label>
          <label className="field"><span>Y</span>
            <input type="number" step="0.05" min="0" max="1" value={yFrac}
                   onChange={(e) => setYFrac(e.target.value)} /></label>
          <label className="field"><span>Scale</span>
            <input type="number" step="0.05" min="0.05" max="1" value={scaleFrac}
                   onChange={(e) => setScaleFrac(e.target.value)} /></label>
        </div>
      )}

      {belowLead && (
        <div className="banner warn">
          Minimum lead is <b>{minLead}s</b> — the overlay will be scheduled at least
          that far ahead so its segments are transcoded in time.
        </div>
      )}

      <button disabled={busy || !channelId || !image} onClick={addOverlay} style={{ width: "100%" }}>
        {busy ? "Working…" : "Schedule overlay"}
      </button>
      {err && <p className="error">{err}</p>}
    </div>
  );
}
