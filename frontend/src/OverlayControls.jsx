import React, { useEffect, useRef, useState } from "react";
import { api, API_BASE } from "./api.js";

const TYPES = [
  ["lband", "L-band", { left: 0, top: 0, width: "24%", height: "100%" }],
  ["top_band", "Top band", { top: 0, left: 0, right: 0, height: "26%" }],
  ["bottom_band", "Bottom band", { bottom: 0, left: 0, right: 0, height: "26%" }],
  ["pip", "PIP", { inset: 0 }],
];

export default function OverlayControls({ channelId, minLead, onChange, onLog }) {
  const [defaults, setDefaults] = useState([]);
  const [image, setImage] = useState(null);      // {image_filename, url}
  const [type, setType] = useState("lband");
  const [startIn, setStartIn] = useState(minLead || 24);
  const [duration, setDuration] = useState(30);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef(null);

  useEffect(() => { api.defaults().then(setDefaults).catch(() => {}); }, []);
  useEffect(() => { setStartIn((v) => (v < (minLead || 0) ? minLead : v)); }, [minLead]);

  function pickDefault(d) {
    setImage({ image_filename: d.image_filename, url: d.url });
    setType(d.overlay_type);
  }
  async function handleFile(file) {
    if (!file) return;
    setErr("");
    try { setBusy(true); setImage(await api.uploadImage(file)); }
    catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }
  async function schedule() {
    if (!image) { setErr("Pick a default, upload, or import an image first."); return; }
    setErr("");
    try {
      setBusy(true);
      await api.createRelative({
        channel_id: channelId, image_filename: image.image_filename,
        overlay_type: type, start_in_seconds: Number(startIn),
        duration_seconds: Number(duration),
      });
      onLog?.(`scheduled ${type} overlay`);
      onChange?.();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  const imgSrc = image ? `${API_BASE}${new URL(image.url, API_BASE).pathname}` : null;

  return (
    <div className="card">
      <h3>Add overlay</h3>

      <span className="hint" style={{ display: "block", marginBottom: 8 }}>Type &amp; placement</span>
      <div className="type-grid">
        {TYPES.map(([v, label, box]) => (
          <div key={v} className={`type-opt ${type === v ? "active" : ""}`} onClick={() => setType(v)}>
            <span className="ic"><i style={box} /></span>{label}
          </div>
        ))}
      </div>

      <span className="hint" style={{ display: "block", margin: "6px 0" }}>Default bands (no upload needed)</span>
      <div className="preset-row">
        {defaults.map((d) => (
          <button key={d.image_filename}
                  className={`preset ${image?.image_filename === d.image_filename ? "active" : ""}`}
                  onClick={() => pickDefault(d)} title={d.label}>
            <img src={`${API_BASE}${new URL(d.url, API_BASE).pathname}`} alt={d.label} />
            <span>{d.label}</span>
          </button>
        ))}
      </div>

      <span className="hint" style={{ display: "block", margin: "6px 0" }}>…or upload your own</span>
      <div className="dropzone" onClick={() => fileRef.current?.click()}
           onDragOver={(e) => e.preventDefault()}
           onDrop={(e) => { e.preventDefault(); handleFile(e.dataTransfer.files?.[0]); }}>
        {image ? "Replace overlay image…" : "Click or drop a PNG (with transparency)"}
        <input ref={fileRef} type="file" accept="image/*" hidden
               onChange={(e) => handleFile(e.target.files?.[0])} />
      </div>
      {imgSrc && <div className="preview"><img src={imgSrc} alt="overlay" /><code>{image.image_filename}</code></div>}

      <div className="row" style={{ marginTop: 10 }}>
        <label className="field"><span>Start in (s)</span>
          <input type="number" min="0" value={startIn} onChange={(e) => setStartIn(e.target.value)} /></label>
        <label className="field"><span>Duration (s)</span>
          <input type="number" min="1" value={duration} onChange={(e) => setDuration(e.target.value)} /></label>
      </div>
      {Number(startIn) < (minLead || 0) && (
        <div className="banner warn">Minimum lead is <b>{minLead}s</b> (transcode headroom for the squeeze). It will be clamped up.</div>
      )}

      <button disabled={busy || !channelId} onClick={schedule} style={{ width: "100%" }}>
        {busy ? "Working…" : "Schedule overlay"}
      </button>
      {err && <p className="error">{err}</p>}
    </div>
  );
}
