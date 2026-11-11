# Keysight Connect — desktop shell

Electron wrapper around the existing app: the React UI in `../web` served by the
FastAPI backend in `../server`. The shell owns the backend process, so the
renderer talks to the same origin and the front-end needs no changes.

```
main.js          window, backend lifecycle, menu, CSP, downloads
preload.js       read-only window.keysight bridge (sandboxed)
splash.html      boot screen shown while uvicorn starts
scripts/build-backend.mjs  freeze server/entrypoint.py with PyInstaller
scripts/build-app.mjs      one-shot pipeline: web + backend + installer
scripts/after-pack.js      ad-hoc codesign after packing (macOS only)
scripts/release.mjs        publish the build as a GitHub release
scripts/make-icon.py       generates build/icon.png + build/icon.ico
scripts/probe.js           reads the live DOM over CDP (verification)
```

Everything is cross-platform: the build pipeline is Node, not bash, and the
app picks the right Python/venv layout and `.exe` suffix on Windows.

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
npm run dist             # host platform: mac arm64, or win x64
npm run dist -- --win    # force Windows (needs Windows, or wine on Linux/mac)
npm run dist -- --mac    # force macOS (needs macOS)
```

That runs `scripts/build-app.mjs`:

1. `npm run build` in `../web` — TypeScript + Vite bundle into `web/dist`
2. `scripts/build-backend.mjs` — PyInstaller freezes `server/entrypoint.py` into
   `backend-dist/keysight-backend/` using `../.venv-desktop` (created on first run)
3. `electron-builder` — packs `main.js`, `preload.js`, `web/dist`, `server/`,
   `smu_core.py` and the frozen backend

Output:

| Host | Artifact |
| --- | --- |
| macOS | `release/mac-arm64/Keysight Connect.app`, `release/Keysight Connect-<version>-arm64.dmg` |
| Windows | `release/win-unpacked/Keysight Connect.exe`, `release/Keysight Connect Setup <version>.exe` (installer), `release/Keysight Connect <version> portable.exe` |

On macOS drag the `.app` to `/Applications`; on Windows run the installer or
just the portable exe.

Publish the build as a GitHub release (needs `gh` authenticated):

```bash
npm run release            # tags v<version>, uploads the platform's artifacts
npm run release -- v0.2.0  # explicit tag
```

`scripts/release.mjs` zips the macOS `.app` with `ditto` so symlinks and the
code signature survive — a plain folder zip produces an app that will not
launch. On Windows it uploads the installer and the portable exe as they are.

### CI

`.github/workflows/desktop.yml` builds both platforms on every `v*` tag (and on
manual dispatch) and uploads the artifacts to the release. That is also how the
Windows build is produced without a Windows machine on hand.

Output is unsigned by Apple but ad-hoc signed by `scripts/after-pack.js`, which
is what makes it launch on Apple Silicon. On another Mac the first launch still
needs right-click → Open, or
`xattr -dr com.apple.quarantine "/Applications/Keysight Connect.app"`, because
there is no notarisation. Signing and notarising properly needs an Apple
Developer ID; add it to the `mac` section of `package.json` when you have one.

## What the packaged app does

- Backend: `Resources/backend/keysight-backend/keysight-backend` on macOS,
  `resources\backend\keysight-backend\keysight-backend.exe` on Windows — frozen,
  no Python install needed on the target machine. The app starts it on every
  launch on a free port and waits for `/api/state` before showing the UI.
- Sessions: `~/Library/Application Support/Keysight Connect/data/sessions/…`
  (macOS) or `%APPDATA%\Keysight Connect\data\sessions\…` (Windows)
- Exports: CSV/ZIP downloads always ask where to save
- Quitting sends SIGTERM to the backend, then SIGKILL after 4 s. If the shell is
  killed outright the backend still exits on its own: on macOS/Linux it polls
  its parent pid, on Windows it watches the stdin pipe the shell handed it for
  EOF (`KEYSIGHT_PARENT_PID`), so no orphan holds the port

## Signing

- macOS: ad-hoc signed by `scripts/after-pack.js` (that is what makes it launch
  on Apple Silicon), not notarised — another Mac needs right-click → Open, or
  `xattr -dr com.apple.quarantine "/Applications/Keysight Connect.app"`.
- Windows: unsigned, so SmartScreen asks once ("More info" → "Run anyway").
  A code-signing certificate can be added to the `win` section of `package.json`
  without code changes.

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
