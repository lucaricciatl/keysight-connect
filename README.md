# keysight connect

Pure-Python control of one or two **Keysight B2901B** precision SMUs over LAN,
with a **web app** (FastAPI + React), a **Tkinter desktop app**, a
hardware-free **mock instrument**, and two end-to-end self-test suites.

No NI-VISA or Keysight IO Libraries needed — `pyvisa-py` plus a TCP socket is
enough (VXI-11 and full VISA resource strings also work).

## What it does

| Feature | Web app | Tkinter app |
| --- | --- | --- |
| Connect one or two B2901B over LAN (socket / VXI-11 / VISA) | yes | yes |
| Source V or I with compliance limit | yes | yes |
| 2-wire / 4-wire (local / remote Kelvin) sensing | yes | — |
| Live V, I, R, P readouts with min/max/mean/std | yes | yes |
| Continuous streaming, every sample in a session CSV | yes | — |
| Synchronised I–V sweep across both units | yes | yes |
| Hardware-timed pulse train | yes | — |
| SCPI console with per-command echo | yes | yes |
| CSV / ZIP session export | yes | — |

Both units can be driven in lockstep with a BUS trigger; if an instrument
refuses BUS triggering the code falls back to parallel triggering and says so
in the log.

## Files

| Path | Purpose |
| --- | --- |
| `smu_core.py` | GUI-free SCPI/VISA layer: connect, source, measure, sweep, pulse, 2W/4W sensing, BUS-triggered sync |
| `server/app.py` | FastAPI backend: REST under `/api/…`, live websocket on `/ws`, serves the built front-end |
| `web/` | React + TypeScript + Vite front-end |
| `desktop/` | Electron shell: owns the backend process, builds a double-clickable `.app` / `.dmg` |
| `server/entrypoint.py` | entry point for the frozen (PyInstaller) desktop backend |
| `keysight_gui.py` | Tkinter front-end: Source/Measure, synchronized I–V sweep, SCPI console |
| `mock_b2901b.py` | Fake B2901B on a TCP port (simulates a resistive load with compliance clipping) |
| `selftest.py` | Exercises `smu_core` against the mocks: single unit, synchronized pair, BUS-trigger fallback |
| `apitest.py` | Boots the backend against two mocks and exercises every REST endpoint the UI uses |
| `gui_smoke.py` | Drives the real Tk app end-to-end against two mock units |

## Requirements

```
pip install pyvisa pyvisa-py fastapi uvicorn
cd web && npm install
```

The web app and the desktop shell need Node 20+; the Tkinter app does not.

Tkinter ships with python.org builds and Homebrew Python; on Debian/Ubuntu
install `python3-tk`.

## Quick start — web app without hardware

```bash
# terminal 1: two fake instruments (100 Ω and 200 Ω loads)
python3 mock_b2901b.py --port 5025 --load 100
python3 mock_b2901b.py --port 5026 --load 200

# terminal 2: the backend + UI
python3 -m server.app --port 8000
```

Open <http://127.0.0.1:8000>, connect A to `127.0.0.1` (port 5025) and B to
`127.0.0.1` (port 5026) in the sidebar, then use the Measure / Sweep / Pulse
tabs.

Front-end development uses Vite with hot reload on <http://127.0.0.1:5173>
(proxying `/api` and `/ws` to port 8000):

```bash
cd web && npm run dev
```

Rebuild the bundle that the backend serves with:

```bash
cd web && npm run build
```

## Desktop app (Electron)

The same UI as a native window — macOS and Windows — with no terminal and no
browser tab. The Electron shell starts the backend itself and points the window
at it, so the front-end is unchanged.

```bash
cd desktop
npm install
npm start          # normal run: backend on a free port
npm run dev        # backend :8000 + Vite :5173, hot reload
npm run dist       # build the host platform's app + installer
```

`npm run dist` runs `desktop/scripts/build-app.mjs`, which bundles the web app,
freezes the backend with PyInstaller, and packs both with electron-builder:

| Host | Artifact |
| --- | --- |
| macOS | `release/mac-arm64/Keysight Connect.app` + `Keysight Connect-<ver>-arm64.dmg` |
| Windows | `release/Keysight Connect Setup <ver>.exe` + portable `.exe` |

The result needs no Python on the target machine; drag it to `/Applications`
or run the installer and double-click.

`.github/workflows/desktop.yml` builds both platforms on every `v*` tag and
attaches them to the release, so a Windows build does not need a Windows
machine. `npm run release` publishes the current platform's build directly.

The bundle is ad-hoc signed, not notarised, so the first launch on another Mac
needs right-click → Open (or `xattr -dr com.apple.quarantine`). Sessions live in
`~/Library/Application Support/Keysight Connect/data/` (File → Open Data Folder).

See `desktop/README.md` for the layout, environment variables and the CDP probe
used to verify a running window.

## Quick start — Tkinter app

```bash
python3 keysight_gui.py --address 127.0.0.1 --address-b 127.0.0.1:5026
```

## 2-wire vs 4-wire sensing

The B2901B can measure either two ways, and the choice changes what the
voltage reading means:

* **2-wire (local sense)** — the same pair of leads carries the current and
  senses the voltage, so the cable and contact resistance (typically a few
  hundred mΩ) is included in the measured voltage. Fine for loads ≫ 1 kΩ.
* **4-wire (remote / Kelvin sense)** — a second pair of sense leads taps the
  voltage directly at the device under test, so lead resistance drops out.
  Required for low-value resistors, contacts, and any measurement where a few
  hundred mΩ matters.

In the web UI the wiring is a **2-wire / 4-wire** selector in the Measure
panel, and each Sweep and Pulse run carries its own selection; the header and
Data tab show what is currently engaged. Selecting 4-wire is refused with a
clear error on the 3 A range, where the instrument does not support remote
sensing.

On the wire the driver sends `:SENSe:REMote ON|OFF` and reads it back, so the
UI reports what the instrument actually accepted rather than what was
requested. Programmatically:

```python
import smu_core
smu = smu_core.B2901B()
smu.open("192.168.1.42")
smu.configure("VOLT", level=0.1, compliance=1e-2, wire="4W")   # or "2W"
print(smu.wire_state())                                       # -> "4W"
```

The mock models the difference too — start it with `--lead 0.5` and a 100 Ω
load reads 100.5 Ω in 2-wire and 100.0 Ω in 4-wire.

## Tests

```bash
python3 selftest.py      # driver checks against the mocks, exit 0 = pass
python3 apitest.py       # backend checks, boots its own mocks on spare ports
python3 gui_smoke.py     # Tk app end-to-end, needs the two mocks running
```

`apitest.py` covers connect, configure (including the 2W/4W round trip and
rejection of unknown modes), measure, streaming, sweep, pulse, SCPI console,
panic and the session exports.

## Real hardware

Give each instrument's LAN address (socket port 5025 is assumed):

```bash
python3 -m server.app --port 8000
python3 keysight_gui.py --address 192.168.1.42 --address-b 192.168.1.43
```

Every command sent to the instruments is echoed in the SCPI Console / console
log, and the backend serialises all instrument traffic on one lock so the UI
never blocks or interleaves commands mid-sweep.

## Session data

Each server start creates `data/sessions/<timestamp>/` containing
`measurements.csv`, `sweeps.csv`, `pulses.csv`, `events.log` and `meta.json`.
Download them individually or as a ZIP from the Data tab, or straight from
`/api/session/export/{measurements|sweeps|pulses|zip}`.
