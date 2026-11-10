# Keysight Connect — desktop shell

Electron wrapper around the existing app: the React UI in `../web` served by the
FastAPI backend in `../server`. The shell owns the backend process, so the
renderer talks to the same origin and the front-end needs no changes.

```
main.js          window, backend lifecycle, menu, CSP, downloads
preload.js       read-only window.keysight bridge (sandboxed)
splash.html      boot screen shown while uvicorn starts
scripts/build-backend.sh   freeze server/entrypoint.py with PyInstaller
scripts/build-app.sh       one-shot pipeline: web + backend + .app/.dmg
scripts/probe.js           reads the live DOM over CDP (verification)
```

## Run it

```bash
cd desktop
npm install
npm start          # backend on a free port, window loads that origin
npm run dev        # backend on :8000 + Vite on :5173 with hot reload
```

`npm run dev` starts Vite itself if it is not already running. The backend is
also auto-started, unless something is already answering on `127.0.0.1:8000`
(handy when you keep a backend running by hand).

## Build a double-clickable app

```bash
npm run dist
```

That runs `scripts/build-app.sh`:

1. `npm --prefix ../web run build` — TypeScript + Vite bundle into `web/dist`
2. `scripts/build-backend.sh` — PyInstaller freezes `server/entrypoint.py` into
   `backend-dist/keysight-backend/` using `../.venv-desktop` (created on first run)
3. `electron-builder --mac --arm64` — packs `main.js`, `preload.js`,
   `web/dist`, `server/`, `smu_core.py` and the frozen backend into
   `release/mac-arm64/Keysight Connect.app`, plus `release/Keysight Connect-<version>-arm64.dmg`

Drag the `.app` to `/Applications`, or open the `.dmg` and drag it across.

Output is unsigned (ad-hoc signature only). On another Mac the first launch
needs right-click → Open, or `xattr -dr com.apple.quarantine "/Applications/Keysight Connect.app"`.
Signing and notarising needs an Apple Developer ID; add it to the `mac`
section of `package.json` when you have one.

## What the packaged app does

- Backend: `Resources/backend/keysight-backend/keysight-backend`, frozen, no
  Python install needed on the target machine
- Sessions: `~/Library/Application Support/Keysight Connect/data/sessions/…`
  (File → Open Data Folder)
- Exports: CSV/ZIP downloads always ask where to save
- Quitting sends SIGTERM to the backend, then SIGKILL after 4 s; no orphan
  uvicorn is left behind

## Environment variables

| Variable | Meaning |
| --- | --- |
| `KEYSIGHT_PYTHON` | interpreter to use for the backend in an unpackaged run |
| `KEYSIGHT_DEV=1` | dev mode (same as `--dev`) |
| `KEYSIGHT_DATA_DIR` | override the session directory the backend writes to |

## Verifying a running window

```bash
./node_modules/.bin/electron . --remote-debugging-port=9222
node scripts/probe.js        # prints the real DOM text + saves a screenshot
```

`probe.js` talks CDP to the renderer, so it reports what is actually on screen
rather than what a screenshot looks like.
