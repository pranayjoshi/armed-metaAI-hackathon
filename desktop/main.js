// Armed desktop shell: one click-through, never-focused card window plus a tray icon.
// The page owns every state; this file owns the window and the runtime process.
const { app, BrowserWindow, Tray, Menu, Notification, nativeImage, screen, shell, session, dialog, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const arg = (name) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : undefined; };
const configPort = /^server:\s*\n(?:[ \t]+.*\n)*?[ \t]+port:\s*(\d+)/m.exec(fs.readFileSync(path.join(ROOT, 'configs/h2m.yaml'), 'utf8'))?.[1];
const PORT = Number(arg('port') ?? process.env.H2M_PORT ?? configPort ?? 8080);
const BASE = `http://127.0.0.1:${PORT}`;
const CORNER = arg('corner') ?? 'tr';
const EXTRA_ARGS = (arg('h2m-args') ?? process.env.H2M_ARGS ?? '').split(/\s+/).filter(Boolean);
const VENV_H2M = path.join(ROOT, '.venv/bin/h2m');
const H2M_CMD = process.env.H2M_CMD ?? (fs.existsSync(VENV_H2M) ? VENV_H2M : 'h2m');
const SIZE = { width: 380, height: 300 }, MARGIN = 16, START_TIMEOUT_MS = 30000;
const COLOURS = { ready: '#5af0a4', moving: '#5af0a4', stopped: '#ff4d5e', fault: '#ff4d5e', disarmed: '#8a8f98', disconnected: '#8a8f98', unreachable: '#ffb020' };

let win, tray, child, quitting = false, crashes = 0, fatal = false, status = 'unreachable', reachable = false;

const request = (url, init = {}) => fetch(BASE + url, { ...init, signal: AbortSignal.timeout(3000) });
const snapshot = () => request('/api/state').then((r) => (r.ok ? r.json() : null), () => null);

// Every POST except stop carries the session token (fresh from `/`, it changes per restart) and our own origin.
async function post(url) {
  const headers = { Origin: BASE };
  if (url !== '/api/stop') {
    const page = await (await request('/')).text();
    headers['X-H2M-Token'] = /name="h2m-token"\s+content="([^"]+)"/.exec(page)?.[1] ?? '';
  }
  return request(url, { method: 'POST', headers });
}
const act = (url) => post(url).then(async (r) => {
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
}).catch((e) => new Notification({ title: 'Armed', body: String(e.message ?? e) }).show());

function startChild() {
  child = spawn(H2M_CMD, ['run', '--port', String(PORT), ...EXTRA_ARGS], { cwd: ROOT, stdio: 'inherit' });
  child.on('error', (e) => { fatal = true; dialog.showErrorBox('Armed', `Could not start "${H2M_CMD}": ${e.message}\nSet H2M_CMD to the h2m executable.`); app.exit(1); });
  child.on('exit', (code) => {
    child = undefined;
    if (quitting || fatal) return;
    if (++crashes > 3) { fatal = true; dialog.showErrorBox('Armed', `The runtime keeps exiting (last code ${code}). Run "h2m run --port ${PORT}" in a terminal to see why.`); app.exit(1); return; }
    setTimeout(startChild, 2000);
  });
}

async function ensureRuntime() {
  if (await snapshot()) return; // attach: not ours, never killed
  startChild();
  for (const end = Date.now() + START_TIMEOUT_MS; Date.now() < end && !fatal; await new Promise((r) => setTimeout(r, 500))) {
    if (await snapshot()) return;
  }
  if (!fatal) dialog.showErrorBox('Armed', `The runtime did not come up on ${BASE} within 30 s.`);
  child?.kill('SIGTERM');
  app.exit(1);
}

function position() {
  const { x, y, width, height } = screen.getPrimaryDisplay().workArea;
  win.setBounds({
    x: CORNER.endsWith('l') ? x + MARGIN : x + width - SIZE.width - MARGIN,
    y: CORNER.startsWith('t') ? y + MARGIN : y + height - SIZE.height - MARGIN,
    ...SIZE,
  });
}

async function loadCard() {
  const card = await request('/card').then((r) => r.ok, () => false);
  win.loadURL(BASE + (card ? '/card' : '/web/card/index.html'));
}

function createWindow() {
  win = new BrowserWindow({
    ...SIZE, show: false, frame: false, transparent: true, hasShadow: false, resizable: false,
    focusable: false, skipTaskbar: true, alwaysOnTop: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false, autoplayPolicy: 'no-user-gesture-required' },
  });
  win.setAlwaysOnTop(true, 'floating');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  position();
  screen.on('display-metrics-changed', position);
  const sameOrigin = (url) => URL.canParse(url) && new URL(url).origin === BASE;
  const guard = (e, url) => { if (!sameOrigin(url)) e.preventDefault(); };
  win.webContents.on('will-navigate', guard);
  win.webContents.on('will-redirect', guard);
  win.webContents.setWindowOpenHandler(({ url }) => { if (sameOrigin(url)) shell.openExternal(url); return { action: 'deny' }; });
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, done) => done(false));
  loadCard();
}

// Page -> shell. Only our own window may ask, and showing never activates.
const fromCard = (fn) => (e, ...a) => { if (win && e.sender === win.webContents) fn(...a); };
ipcMain.on('host:visible', fromCard((v) => (v ? win.showInactive() : win.hide())));
ipcMain.on('host:interactive', fromCard((v) => (v ? win.setIgnoreMouseEvents(false) : win.setIgnoreMouseEvents(true, { forward: true }))));
ipcMain.on('host:stop', fromCard(() => act('/api/stop')));
ipcMain.on('host:console', fromCard(() => shell.openExternal(BASE + '/')));

function dot(hex) { // 16 pt anti-aliased disc at 2x, premultiplied BGRA
  const n = 32, buf = Buffer.alloc(n * n * 4), [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const a = Math.min(1, Math.max(0, 11.5 - Math.hypot(x - 15.5, y - 15.5))), o = (y * n + x) * 4;
    buf.set([b * a, g * a, r * a, a * 255], o);
  }
  return nativeImage.createFromBitmap(buf, { width: n, height: n, scaleFactor: 2 });
}

function refreshTray() {
  const armState = status;
  const toggle = { disarmed: ['Arm', '/api/arm/arm'], stopped: ['Reset (then Arm)', '/api/arm/reset'], fault: ['Reset (then Arm)', '/api/arm/reset'] }[armState] ?? ['Disarm', '/api/arm/disarm'];
  tray.setImage(dot(COLOURS[armState] ?? COLOURS.unreachable));
  tray.setToolTip(`Armed: ${armState}`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: `Arm is ${armState}`, enabled: false },
    { label: toggle[0], enabled: reachable, click: () => act(toggle[1]) },
    { label: 'Stop', enabled: reachable, click: () => act('/api/stop') },
    { label: 'Open console', click: () => shell.openExternal(BASE + '/') },
    { type: 'separator' },
    { label: 'Quit (stops the arm)', click: () => app.quit() },
  ]));
}

async function poll() {
  const s = await snapshot();
  const next = s ? s.arm?.state ?? 'unreachable' : 'unreachable';
  if (s && !reachable && win) loadCard(); // runtime (re)appeared: fetch the page again for its new token
  if (s) crashes = 0;
  if (next !== status || !!s !== reachable) { status = next; reachable = !!s; refreshTray(); }
  setTimeout(poll, 1000);
}

// Quit stops the arm first, then ends only a runtime this shell started.
app.on('before-quit', async (e) => {
  if (quitting) return;
  e.preventDefault();
  quitting = true;
  await post('/api/stop').catch(() => {});
  if (child) { child.kill('SIGTERM'); await new Promise((r) => { child.once('exit', r); setTimeout(r, 5000); }); }
  app.exit(0);
});
['SIGINT', 'SIGTERM'].forEach((sig) => process.on(sig, () => app.quit()));
app.on('window-all-closed', () => {}); // the tray keeps the shell alive

if (!app.requestSingleInstanceLock()) app.exit(0);
app.whenReady().then(async () => {
  app.dock?.hide();
  tray = new Tray(dot(COLOURS.unreachable));
  refreshTray();
  await ensureRuntime();
  reachable = true; // so the first poll does not reload the page we are about to load
  createWindow();
  poll();
});
