// Backend base URL. Override at build/run time with VITE_API_BASE.
export const API_BASE =
  import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function req(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error(typeof data === "string" ? data : (data?.detail || res.status));
  return data;
}

export const api = {
  // channels (common + channel-specific)
  ingest: (master_url, name) => req("POST", "/api/ingest", { master_url, name }),
  listChannels: () => req("GET", "/api/channels"),
  getChannel: (id) => req("GET", `/api/channels/${id}`),
  updateChannel: (id, patch) => req("PUT", `/api/channels/${id}`, patch),
  stopChannel: (id) => req("POST", `/api/channels/${id}/stop`),
  startChannel: (id) => req("POST", `/api/channels/${id}/start`),
  deleteChannel: (id) => req("DELETE", `/api/channels/${id}`),
  channelStatus: (id) => req("GET", `/api/channels/${id}/status`),
  channelDebug: (id, v = 0) => req("GET", `/api/channels/${id}/debug?variant_index=${v}`),

  // overlays
  listOverlays: (id) => req("GET", `/api/channels/${id}/overlays`),
  createRelative: (payload) => req("POST", "/api/overlays/relative", payload),
  createAbsolute: (payload) => req("POST", "/api/overlays", payload),
  deleteOverlay: (id) => req("DELETE", `/api/overlays/${id}`),
  defaults: () => req("GET", "/api/defaults"),
  fromUrl: (url) => req("POST", "/api/overlays/from-url", { url }),
  async uploadImage(file) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API_BASE}/api/overlays/upload`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed: ${res.status}`);
    return res.json();
  },

  health: () => req("GET", "/api/health"),
  masterUrl: (id) => `${API_BASE}/hls/${id}/master.m3u8`,
  raw: req,
};
