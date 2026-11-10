'use strict';

/**
 * Keysight Connect — Electron shell.
 *
 * The UI is the existing React app; the instrument logic is the existing
 * FastAPI backend (server/app.py).  This process owns the backend lifecycle:
 *
 *   npm start   backend on an ephemeral port  ->  window loads that origin
 *   npm run dev  backend on :8000 + Vite :5173 ->  window loads the dev server
 *
 * Everything the renderer needs (REST /api, WebSocket /ws, /assets) is same
 * origin, so the front-end needs no changes at all.
 */

const { app, BrowserWindow, Menu, dialog, shell, session, clipboard } = require('electron');
const { spawn } = require('node:child_process');
const net = require('node:net');
const path = require('node:path');
const fs = require('node:fs');

const isDev = process.env.KEYSIGHT_DEV === '1' || process.argv.includes('--dev');

// Without this the macOS menu bar says "Electron" in an unpackaged build.
app.setName('Keysight Connect');

// In dev the repo root is simply the parent of desktop/.  Packaged, the same
// tree (smu_core.py, server/, web/dist/) is copied to Resources/backend.
const BACKEND_DIR = app.isPackaged
  ? path.join(process.resourcesPath, 'backend')
  : path.join(__dirname, '..');

const WEB_DIR = path.join(BACKEND_DIR, 'web');
const DEV_BACKEND_PORT = 8000;
const VITE_URL = 'http://127.0.0.1:5173';
const BOOT_TIMEOUT_MS = 45000;

let backend = null;          // ChildProcess
let backendUrl = null;       // http://127.0.0.1:<port>
let frontendUrl = null;      // where the window should point once ready
let win = null;
let dataDir = null;
let quitting = false;
let vite = null;
const backendTail = [];      // last lines, for the failure dialog

const log = (...args) => console.log('[keysight]', ...args);

/* ------------------------------------------------------------------ utils */

function remember(line) {
  backendTail.push(line);
  if (backendTail.length > 300) backendTail.shift();
}

function exists(p) {
  try { return fs.existsSync(p); } catch { return false; }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function httpOk(url, timeoutMs = 1200) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    return res.ok;
  } catch {
    return false;
  }
}

async function waitForHttp(url, timeoutMs, alive) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (alive && !alive()) throw new Error('process exited before serving');
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return;
      lastError = new Error(`HTTP ${res.status} from ${url}`);
    } catch (err) {
      lastError = err;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out after ${timeoutMs / 1000}s waiting for ${url}` +
    (lastError ? ` (${lastError.message})` : ''));
}

/* ---------------------------------------------------------------- backend */

function findPython() {
  const candidates = [
    process.env.KEYSIGHT_PYTHON,
    path.join(BACKEND_DIR, '.venv', 'bin', 'python3'),
    path.join(BACKEND_DIR, 'venv', 'bin', 'python3'),
  ].filter(Boolean);
  for (const c of candidates) if (exists(c)) return c;
  // Fall back to whatever is on PATH.
  return ['python3', 'python'].find((p) => p) || 'python3';
}

function pipeLines(stream, tag) {
  if (!stream) return;
  stream.setEncoding('utf8');
  let buf = '';
  stream.on('data', (chunk) => {
    buf += chunk;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).replace(/\r$/, '');
      buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      log(`[backend:${tag}]`, line);
      remember(line);
    }
  });
}

async function startBackend() {
  const bundled = path.join(BACKEND_DIR, 'keysight-backend', 'keysight-backend');
  const port = isDev ? DEV_BACKEND_PORT : await freePort();
  const url = `http://127.0.0.1:${port}`;

  // A backend may already be running (dev workflow: you started it by hand).
  if (await httpOk(`${url}/api/state`)) {
    log(`reusing backend already listening on ${url}`);
    backendUrl = url;
    return url;
  }

  let cmd;
  let args;
  if (exists(bundled)) {
    cmd = bundled;
    args = ['--host', '127.0.0.1', '--port', String(port)];
  } else {
    cmd = findPython();
    args = ['-m', 'server.app', '--host', '127.0.0.1', '--port', String(port)];
  }

  log(`starting backend: ${cmd} ${args.join(' ')}`);
  log(`cwd: ${BACKEND_DIR}`);

  backend = spawn(cmd, args, {
    cwd: BACKEND_DIR,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      PYTHONDONTWRITEBYTECODE: '1',
      KEYSIGHT_DATA_DIR: dataDir,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  pipeLines(backend.stdout, 'out');
  pipeLines(backend.stderr, 'err');

  backend.on('error', (err) => {
    remember(`spawn failed: ${err.message}`);
    log(`backend spawn failed: ${err.message}`);
  });

  backend.on('exit', (code, signal) => {
    log(`backend exited (code=${code} signal=${signal})`);
    backend = null;
    if (!quitting && win) {
      dialog.showMessageBox(win, {
        type: 'error',
        message: 'Instrument service stopped unexpectedly',
        detail: backendTail.slice(-15).join('\n') || `exit code ${code}`,
        buttons: ['Quit'],
      }).then(() => app.quit());
    }
  });

  await waitForHttp(`${url}/api/state`, BOOT_TIMEOUT_MS, () => backend !== null);
  backendUrl = url;
  log(`backend ready at ${url}`);
  return url;
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  log('stopping backend');
  const child = backend;
  backend = null;
  child.kill('SIGTERM');
  const timer = setTimeout(() => {
    try { child.kill('SIGKILL'); } catch { /* already gone */ }
  }, 4000);
  timer.unref?.();
}

/* ------------------------------------------------------------ dev server */

async function ensureVite() {
  if (await httpOk(VITE_URL, 800)) {
    log(`reusing Vite dev server at ${VITE_URL}`);
    return VITE_URL;
  }
  if (!exists(path.join(WEB_DIR, 'node_modules'))) {
    log('web/node_modules missing — cannot start Vite, falling back to the built bundle');
    return null;
  }
  log('starting Vite dev server');
  vite = spawn('npm', ['run', 'dev', '--silent'], {
    cwd: WEB_DIR,
    env: { ...process.env, KEYSIGHT_BACKEND: `http://127.0.0.1:${DEV_BACKEND_PORT}` },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  vite.stdout.setEncoding('utf8');
  vite.stdout.on('data', (d) => process.stdout.write(`[vite] ${d}`));
  vite.stderr.setEncoding('utf8');
  vite.stderr.on('data', (d) => process.stderr.write(`[vite] ${d}`));
  try {
    await waitForHttp(VITE_URL, 40000);
    return VITE_URL;
  } catch (err) {
    log(`Vite failed to start (${err.message}) — falling back to the built bundle`);
    return null;
  }
}

function stopVite() {
  if (!vite || vite.exitCode !== null) return;
  log('stopping Vite');
  const child = vite;
  vite = null;
  child.kill('SIGTERM');
}

/* ---------------------------------------------------------------- window */

function createWindow() {
  win = new BrowserWindow({
    width: 1500,
    height: 940,
    minWidth: 1120,
    minHeight: 720,
    title: 'Keysight Connect',
    backgroundColor: '#060a0f',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  win.once('ready-to-show', () => win.show());
  win.on('closed', () => { win = null; });

  // Links open in the real browser, never inside the instrument window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    const allowed = [frontendUrl, backendUrl].filter(Boolean);
    if (!allowed.some((base) => url.startsWith(base))) {
      event.preventDefault();
      if (/^https?:/.test(url)) shell.openExternal(url);
    }
  });

  win.loadFile(path.join(__dirname, 'splash.html'));
  return win;
}

function showApp() {
  if (!win) createWindow();
  if (!frontendUrl) return;
  log(`loading ${frontendUrl}`);
  win.loadURL(frontendUrl);
}

/* ------------------------------------------------------------ app chrome */

function applyCsp() {
  // Dev only runs against Vite, which needs eval/inline for HMR — leave it alone.
  if (isDev) return;
  const csp = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' ws: http://127.0.0.1:*",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
  ].join('; ');

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    if (!details.url.startsWith('http://127.0.0.1')) {
      return callback({ responseHeaders: details.responseHeaders });
    }
    const headers = { ...(details.responseHeaders || {}) };
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'content-security-policy') delete headers[key];
    }
    headers['Content-Security-Policy'] = [csp];
    callback({ responseHeaders: headers });
  });
}

function applyDownloadHandling() {
  // Session exports are CSV/ZIP: always ask where to put them.
  session.defaultSession.on('will-download', (event, item) => {
    const name = item.getFilename();
    const ext = path.extname(name).replace(/^\./, '');
    const filters = ext
      ? [{ name: `${ext.toUpperCase()} file`, extensions: [ext] }, { name: 'All files', extensions: ['*'] }]
      : [{ name: 'All files', extensions: ['*'] }];
    item.setSaveDialogOptions({
      title: 'Save session export',
      defaultPath: path.join(app.getPath('downloads'), name),
      filters,
    });
  });
}

function buildMenu() {
  const template = [];

  if (process.platform === 'darwin') {
    template.push({
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    });
  }

  template.push({
    label: 'File',
    submenu: [
      {
        label: 'Open Data Folder',
        click: () => { fs.mkdirSync(dataDir, { recursive: true }); shell.openPath(dataDir); },
      },
      {
        label: 'Copy Backend Address',
        enabled: Boolean(backendUrl),
        click: () => backendUrl && clipboard.writeText(backendUrl),
      },
      { type: 'separator' },
      process.platform === 'darwin' ? { role: 'close' } : { role: 'quit' },
    ],
  });

  template.push({
    label: 'View',
    submenu: [
      { role: 'reload' },
      { role: 'forceReload' },
      ...(isDev ? [{ role: 'toggleDevTools' }] : []),
      { type: 'separator' },
      { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
      { type: 'separator' },
      { role: 'togglefullscreen' },
    ],
  });

  template.push({
    label: 'Window',
    submenu: [{ role: 'minimize' }, { role: 'zoom' }, { role: 'front' }],
  });

  template.push({
    role: 'help',
    submenu: [
      {
        label: 'Project README',
        click: () => shell.openPath(path.join(BACKEND_DIR, 'README.md')),
      },
      {
        label: 'Front-end dev server',
        visible: isDev,
        click: () => shell.openExternal(VITE_URL),
      },
    ],
  });

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/* ------------------------------------------------------------- lifecycle */

async function boot() {
  dataDir = app.isPackaged
    ? path.join(app.getPath('userData'), 'data')
    : path.join(BACKEND_DIR, 'data');
  fs.mkdirSync(dataDir, { recursive: true });
  log(`data dir: ${dataDir}`);

  applyCsp();
  applyDownloadHandling();
  createWindow();

  try {
    await startBackend();
    let devUrl = null;
    if (isDev) devUrl = await ensureVite();
    frontendUrl = devUrl || backendUrl;
    buildMenu();
    showApp();
  } catch (err) {
    log(`boot failed: ${err.message}`);
    const detail = [
      err.message,
      '',
      'Last backend output:',
      ...(backendTail.slice(-20).length ? backendTail.slice(-20) : ['(none)']),
      '',
      exists(path.join(BACKEND_DIR, 'keysight-backend', 'keysight-backend'))
        ? ''
        : `Python: ${process.env.KEYSIGHT_PYTHON || 'python3 from PATH'}`,
      'The backend needs fastapi, uvicorn, pyvisa and pyvisa-py:',
      '  pip3 install fastapi uvicorn pyvisa pyvisa-py',
    ].join('\n');
    await dialog.showMessageBox(win, {
      type: 'error',
      message: 'Could not start the instrument service',
      detail,
      buttons: ['Quit'],
    });
    quitting = true;
    app.quit();
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(boot);

  app.on('activate', () => {
    if (!win) {
      createWindow();
      if (frontendUrl) win.loadURL(frontendUrl);
    }
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });

  app.on('before-quit', () => {
    quitting = true;
    stopVite();
    stopBackend();
  });

  app.on('will-quit', () => {
    stopVite();
    stopBackend();
  });
}
