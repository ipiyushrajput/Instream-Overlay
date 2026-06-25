// Backend base URL. Override at build/run time with VITE_API_BASE.
export const API_BASE =
  import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function json(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${await res.text()}`);
  return res.json();
}

export const api = {
  ingest: (master_url, name) => json("POST", "/api/ingest", { master_url, name }),
  getChannel: (id) => json("GET", `/api/channels/${id}`),
  stopChannel: (id) => json("DELETE", `/api/channels/${id}`),
  channelStatus: (id) => json("GET", `/api/channels/${id}/status`),
  listOverlays: (id) => json("GET", `/api/channels/${id}/overlays`),
  createRelative: (payload) => json("POST", "/api/overlays/relative", payload),
  deleteOverlay: (id) => json("DELETE", `/api/overlays/${id}`),

  async uploadImage(file) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API_BASE}/api/overlays/upload`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed: ${res.status}`);
    return res.json();
  },

  masterUrl: (id) => `${API_BASE}/hls/${id}/master.m3u8`,
};
