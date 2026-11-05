// The Card's line to the runtime: session token, the /api/live stream, and POSTs.
// Self-contained on purpose (the Card is not the console shell): it needs no core.js side effects.

const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content || "";

let token = meta("armed-token");
let mode = meta("armed-mode") === "replay" ? "replay" : "live";
let bootstrap = null;

/** The server writes the token only into pages it renders (`/`, later `/card`). Served as a plain
 * file this page reads it from `/` the way scripts do (docs/LOCAL_API.md). Resolves to the token
 * ("" when it could not be read); the replay flag is learned from the same page. */
export function ensureToken() {
  if (token) return Promise.resolve(token);
  bootstrap ||= fetch("/", { cache: "no-store" })
    .then((r) => r.text())
    .then((html) => {
      token = /name="armed-token"\s+content="([^"]*)"/.exec(html)?.[1] || "";
      if (/name="armed-mode"\s+content="replay"/.test(html)) mode = "replay";
      if (!token) bootstrap = null; // try again on the next call
      return token;
    })
    .catch(() => {
      bootstrap = null;
      return "";
    });
  return bootstrap;
}
export const isReplayPage = () => mode === "replay";

/** POST JSON with the token. Never throws: {ok, status, detail}. `/api/stop` is tokenless on the
 * server, so STOP is sent even when the token could not be read. */
export async function post(path, body) {
  const headers = { "Content-Type": "application/json" };
  const t = await ensureToken();
  if (t) headers["X-Armed-Token"] = t;
  try {
    const res = await fetch(path, { method: "POST", headers, body: JSON.stringify(body ?? {}) });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, detail: data.detail ?? (res.ok ? "" : res.statusText) };
  } catch (err) {
    return { ok: false, status: 0, detail: "the runtime is not reachable" };
  }
}

/** Subscribe to /api/live. onState(snapshot), onConn(bool). Reconnects; a stream that stops talking
 * for 2.5 s counts as lost (the runtime sends every 0.1 s). */
export function connect(onState, onConn) {
  let ws = null;
  let last = 0;
  let up = false;
  const set = (v) => {
    if (v !== up) {
      up = v;
      onConn(v);
    }
  };
  const open = () => {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`);
    ws.onmessage = (m) => {
      last = performance.now();
      set(true);
      try {
        onState(JSON.parse(m.data));
      } catch (err) {
        console.error("[card] bad snapshot", err);
      }
    };
    ws.onclose = () => {
      set(false);
      setTimeout(open, 1000);
    };
    ws.onerror = () => ws.close();
  };
  open();
  setInterval(() => {
    if (up && performance.now() - last > 2500) set(false);
  }, 500);
}
