import React, { useRef, useState } from "react";
import { api, API_BASE } from "./api.js";

const TYPES = [
  ["lband", "L-band (bottom)"],
  ["lower_third", "Lower third"],
  ["top_banner", "Top banner"],
  ["full_frame", "Full frame"],
  ["custom", "Custom position"],
];

export default function OverlayControls({ channelId, onChange }) {
  const [image, setImage] = useState(null); // {image_filename, url}
  const [type, setType] = useState("lband");
  const [startIn, setStartIn] = useState(8);
  const [duration, setDuration] = useState(18);
  const [xFrac, setXFrac] = useState(0.5);
  const [yFrac, setYFrac] = useState(0.85);
  const [scaleFrac, setScaleFrac] = useState(0.4);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef(null);

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setErr("");
    try {
      setBusy(true);
      const res = await api.uploadImage(file);
      setImage(res);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addOverlay() {
    if (!channelId || !image) {
      setErr("Pick a channel and upload an overlay image first.");
      return;
    }
    setErr("");
    try {
      setBusy(true);
      await api.createRelative({
        channel_id: channelId,
        image_filename: image.image_filename,
        overlay_type: type,
        start_in_seconds: Number(startIn),
        duration_seconds: Number(duration),
        x_frac: Number(xFrac),
        y_frac: Number(yFrac),
        scale_frac: Number(scaleFrac),
      });
      onChange?.();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <h3>Add overlay</h3>

      <label className="field">
        <span>Overlay image (PNG with alpha)</span>
        <input ref={fileRef} type="file" accept="image/*" onChange={handleUpload} />
      </label>
      {image && (
        <div className="preview">
          <img src={`${API_BASE}${new URL(image.url).pathname}`} alt="overlay" />
          <code>{image.image_filename}</code>
        </div>
      )}

      <label className="field">
        <span>Type</span>
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {TYPES.map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
      </label>

      <div className="row">
        <label className="field">
          <span>Start in (s)</span>
          <input type="number" min="0" value={startIn}
                 onChange={(e) => setStartIn(e.target.value)} />
        </label>
        <label className="field">
          <span>Duration (s)</span>
          <input type="number" min="1" value={duration}
                 onChange={(e) => setDuration(e.target.value)} />
        </label>
      </div>

      {type === "custom" && (
        <div className="row">
          <label className="field"><span>X frac</span>
            <input type="number" step="0.05" min="0" max="1" value={xFrac}
                   onChange={(e) => setXFrac(e.target.value)} /></label>
          <label className="field"><span>Y frac</span>
            <input type="number" step="0.05" min="0" max="1" value={yFrac}
                   onChange={(e) => setYFrac(e.target.value)} /></label>
          <label className="field"><span>Scale</span>
            <input type="number" step="0.05" min="0.05" max="1" value={scaleFrac}
                   onChange={(e) => setScaleFrac(e.target.value)} /></label>
        </div>
      )}

      <button disabled={busy || !channelId} onClick={addOverlay}>
        {busy ? "Working…" : "Schedule overlay"}
      </button>
      {err && <p className="error">{err}</p>}
      <p className="hint">
        The overlay window is relative to the live edge. Because we hold the
        output behind live by a few segments, give it a few seconds of lead time.
      </p>
    </div>
  );
}
