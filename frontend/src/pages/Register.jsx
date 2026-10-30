import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { PageBody, PageHeader } from "../PageHeader.jsx";

/* Prefill for the origin field. Empty by default so the build is tied to no
   machine; set VITE_DEFAULT_ORIGIN at build time to pre-populate it. */
const DEFAULT_ORIGIN = import.meta.env.VITE_DEFAULT_ORIGIN || "";

export default function Register() {
  const [name, setName] = useState("");
  const [masterUrl, setMasterUrl] = useState(DEFAULT_ORIGIN);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  async function register() {
    setErr("");
    try {
      setBusy(true);
      const ch = await api.ingest(masterUrl, name || "channel");
      nav(`/channel/${ch.id}`);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  return (
    <div>
      <PageHeader title="Register a channel"
                  subtitle="Point the console at an HLS origin; its manifests are mirrored and overlays splice in." />
      <PageBody>
      <div className="card narrow-card">
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
          The channel is saved to the database and stays in your list even after
          you stop ingestion.
        </p>
      </div>
      </PageBody>
    </div>
  );
}
