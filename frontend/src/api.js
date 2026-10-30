/**
 * Backend base URL.
 *
 * Empty means "same origin", which is what you want behind a reverse proxy: nginx
 * serves this bundle and proxies the API on one host, so the build carries no
 * machine, IP or port and the same artefact runs anywhere (Windows or Linux).
 *
 * Set VITE_API_BASE at build time only when the API lives somewhere else. A dev
 * run (`npm run dev`) defaults to the backend on :8000 because Vite serves the
 * page from :5173, which is a different origin.
 */
const RAW_BASE = import.meta.env.VITE_API_BASE;
export const API_BASE = (
  RAW_BASE === undefined || RAW_BASE === null
    ? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "")
    : RAW_BASE
).replace(/\/+$/, "");

/** Absolute http(s) URL for a backend path — safe to copy, paste or hand to a player. */
export function absUrl(path) {
  if (API_BASE) return `${API_BASE}${path}`;
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `${origin}${path}`;
}

/** ws:// or wss:// URL for a backend path, derived from the page when same-origin. */
export function wsUrl(path) {
  if (API_BASE) return API_BASE.replace(/^http/, "ws") + path;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

/**
 * Re-point a URL the backend built from PUBLIC_BASE_URL at the origin the browser
 * actually uses, so an asset still loads when the two differ (behind a proxy, or
 * when PUBLIC_BASE_URL names an address only the server can reach).
 */
export function sameOrigin(url) {
  try {
    const u = new URL(url, typeof window === "undefined" ? undefined : window.location.origin);
    return absUrl(`${u.pathname}${u.search}`);
  } catch {
    return url;
  }
}

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
  insights: () => req("GET", "/api/insights"),
  channelInsights: (id) => req("GET", `/api/channels/${id}/insights`),

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
  masterUrl: (id) => absUrl(`/hls/${id}/master.m3u8`),
  raw: req,
};
