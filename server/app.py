"""FastAPI backend for the Keysight B2901B web app.

Wraps smu_core.B2901B / SyncGroup and exposes:

    REST   /api/...        commands, state, session exports
    WS     /ws             live readings, sweep/pulse points, SCPI log

Everything that touches an instrument is serialised on IO_LOCK, because a
single VISA session is not safe to drive from two threads at once.  Endpoints
are plain ``def`` so FastAPI runs them in its worker pool and the event loop
stays free for the websocket.

Run:
    python3 -m server.app --port 8000
    # or
    uvicorn server.app:app --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import re
import sys
import threading
import time
import uuid
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import smu_core as smu

ROOT = Path(__file__).resolve().parent.parent
# The Electron shell sets KEYSIGHT_DATA_DIR so a packaged app writes sessions
# to its user-data folder instead of inside the (read-only) app bundle.
DATA_DIR = Path(os.environ.get("KEYSIGHT_DATA_DIR", ROOT / "data")).expanduser()


def _find_dist() -> Path:
    """Locate the built front-end.

    From a checkout this is `<repo>/web/dist`.  Inside a frozen (PyInstaller)
    build the module path no longer points at the repo, so fall back to the
    layout electron-builder produces: Resources/backend/web/dist two levels
    above the executable, which lives in Resources/backend/keysight-backend/.
    """
    override = os.environ.get("KEYSIGHT_DIST")
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (exe_dir.parent.parent / "web" / "dist",
                          exe_dir / "web" / "dist",
                          exe_dir.parent / "web" / "dist"):
            if candidate.is_dir():
                return candidate
    return ROOT / "web" / "dist"


DIST = _find_dist()

IO_LOCK = threading.RLock()
APP_STARTED = time.time()


def _watch_parent() -> None:
    """Shut down if the desktop shell that spawned us goes away.

    POSIX reparents orphans, so polling getppid() is enough there.  Windows
    does not, so the shell hands us an open, never-written stdin pipe and we
    watch it for EOF instead.  Only active when KEYSIGHT_PARENT_PID is set, so
    `python3 -m server.app` is unaffected.
    """
    raw = os.environ.get("KEYSIGHT_PARENT_PID")
    if not raw:
        return
    try:
        parent = int(raw)
    except ValueError:
        return

    def shutdown() -> None:
        try:
            add_log(None, "desktop shell exited, shutting down", "info")
        except Exception:                              # noqa: BLE001
            pass
        os._exit(0)

    if os.name == "nt":
        def watch_stdin() -> None:
            try:
                while sys.stdin.buffer.read(1):
                    pass
            except Exception:                          # noqa: BLE001
                pass
            shutdown()

        threading.Thread(target=watch_stdin, daemon=True,
                         name="parent-watch").start()
    else:
        def watch_ppid() -> None:
            while True:
                time.sleep(2.0)
                if os.getppid() != parent:
                    shutdown()

        threading.Thread(target=watch_ppid, daemon=True,
                         name="parent-watch").start()


_watch_parent()


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def metrics(volts: Optional[float], amps: Optional[float]) -> dict:
    """Derive R and P from a V/I pair, keeping None instead of dividing by 0."""
    v = volts
    i = amps
    ohms = None
    if i is not None and i != 0.0 and v is not None:
        ohms = v / i
    watts = None if (v is None or i is None) else v * i
    return {"volts": v, "amps": i, "ohms": ohms, "watts": watts}


class Stats:
    """Running statistics for one device, exposed to the UI readouts."""

    KEYS = ("volts", "amps", "ohms", "watts")

    def __init__(self) -> None:
        self.n = 0
        self._lock = threading.Lock()
        self.acc = {k: {"count": 0, "min": None, "max": None,
                        "mean": None, "m2": 0.0} for k in self.KEYS}

    def add(self, m: dict) -> None:
        with self._lock:
            self.n += 1
            for key in self.KEYS:
                acc = self.acc[key]
                value = m.get(key)
                if value is None:
                    continue
                acc["count"] += 1
                acc["min"] = value if acc["min"] is None else min(acc["min"], value)
                acc["max"] = value if acc["max"] is None else max(acc["max"], value)
                if acc["mean"] is None:
                    acc["mean"] = value
                    acc["m2"] = 0.0
                else:
                    delta = value - acc["mean"]
                    acc["mean"] += delta / acc["count"]
                    acc["m2"] += delta * (value - acc["mean"])

    def reset(self) -> None:
        with self._lock:
            self.n = 0
            for acc in self.acc.values():
                acc.update({"count": 0, "min": None, "max": None,
                            "mean": None, "m2": 0.0})

    def snapshot(self) -> dict:
        with self._lock:
            out: dict[str, Any] = {"n": self.n}
            for key in self.KEYS:
                acc = self.acc[key]
                std = None
                if acc["count"] > 1 and acc["mean"] is not None:
                    std = (max(acc["m2"], 0.0) / (acc["count"] - 1)) ** 0.5
                out[key] = {"min": acc["min"], "max": acc["max"],
                            "mean": acc["mean"], "std": std,
                            "count": acc["count"]}
            return out


# ---------------------------------------------------------------------------
# session recorder -- everything the user measures lands on disk
# ---------------------------------------------------------------------------

class Session:
    """One measurement session: a directory of CSV files plus metadata."""

    MEAS_HEADER = ["timestamp", "iso", "device", "volts", "amps", "ohms", "watts"]
    SWEEP_HEADER = ["run_id", "iso", "index", "device", "source_value",
                    "volts", "amps", "ohms", "watts"]
    PULSE_HEADER = ["run_id", "iso", "device", "index", "source_value",
                    "volts", "amps", "ohms", "watts"]

    def __init__(self, name: str = "") -> None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.id = stamp
        self.dir = DATA_DIR / "sessions" / stamp
        if name:
            self.dir = DATA_DIR / "sessions" / f"{stamp}_{_slug(name)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.lock = threading.Lock()
        self.counts = {"measurements": 0, "sweep_points": 0, "pulse_points": 0}
        self.created = now_iso()
        self.meas_path = self.dir / "measurements.csv"
        self.sweep_path = self.dir / "sweeps.csv"
        self.pulse_path = self.dir / "pulses.csv"
        self.events_path = self.dir / "events.log"
        for path, header in ((self.meas_path, self.MEAS_HEADER),
                             (self.sweep_path, self.SWEEP_HEADER),
                             (self.pulse_path, self.PULSE_HEADER)):
            if not path.exists():
                with path.open("w", newline="") as fh:
                    csv.writer(fh).writerow(header)
        _touch(self.events_path)
        self.save_meta()

    # -- writers ---------------------------------------------------------
    def _append(self, path: Path, header: list[str], row: list) -> None:
        with self.lock:
            with path.open("a", newline="") as fh:
                csv.writer(fh).writerow(row)

    @staticmethod
    def _row(ts: float, iso: str) -> list:
        return [f"{ts:.6f}", iso]

    def add_measurement(self, device: str, m: dict, ts: float, iso: str) -> None:
        self._append(self.meas_path, self.MEAS_HEADER,
                     self._row(ts, iso) + [device, _f(m["volts"]), _f(m["amps"]),
                                           _f(m["ohms"]), _f(m["watts"])])
        with self.lock:
            self.counts["measurements"] += 1
        self.save_meta()

    def add_sweep_point(self, run_id: str, index: int, device: str,
                        x: float, m: dict, iso: str) -> None:
        self._append(self.sweep_path, self.SWEEP_HEADER,
                     [run_id, iso, index, device, _f(x), _f(m["volts"]),
                      _f(m["amps"]), _f(m["ohms"]), _f(m["watts"])])
        with self.lock:
            self.counts["sweep_points"] += 1
        self.save_meta()

    def add_pulse_point(self, run_id: str, device: str, index: int,
                        x: float, m: dict, iso: str) -> None:
        self._append(self.pulse_path, self.PULSE_HEADER,
                     [run_id, iso, device, index, _f(x), _f(m["volts"]),
                      _f(m["amps"]), _f(m["ohms"]), _f(m["watts"])])
        with self.lock:
            self.counts["pulse_points"] += 1
        self.save_meta()

    def event(self, text: str) -> None:
        with self.lock:
            with self.events_path.open("a") as fh:
                fh.write(f"{now_iso()}  {text}\n")

    # -- metadata --------------------------------------------------------
    def meta(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "dir": str(self.dir),
            "created": self.created,
            "counts": dict(self.counts),
            "files": [
                {"name": f.name, "size": f.stat().st_size}
                for f in (self.meas_path, self.sweep_path, self.pulse_path,
                          self.events_path) if f.exists()
            ],
        }

    def save_meta(self) -> None:
        try:
            (self.dir / "meta.json").write_text(json.dumps(self.meta(), indent=2))
        except OSError:
            pass

    def zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in (self.meas_path, self.sweep_path, self.pulse_path,
                         self.events_path, self.dir / "meta.json"):
                if path.exists():
                    zf.write(path, arcname=path.name)
        return buf.getvalue()


def _slug(text: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "-" for c in text)
    return keep.strip("-")[:40] or "session"


def _f(value: Optional[float]) -> str:
    return "" if value is None else f"{value:.12g}"


def _touch(path: Path) -> None:
    if not path.exists():
        path.write_text("")


# ---------------------------------------------------------------------------
# websocket fan-out (thread safe: producer threads -> asyncio queues)
# ---------------------------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self.queues: set[asyncio.Queue] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def attach(self) -> None:
        self.loop = asyncio.get_running_loop()

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.queues.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self.queues.discard(q)

    def publish(self, msg: dict) -> None:
        if not self.queues or self.loop is None:
            return
        try:
            payload = json.dumps(msg, default=str)
        except (TypeError, ValueError):
            return
        for q in list(self.queues):
            try:
                self.loop.call_soon_threadsafe(self._offer, q, payload)
            except RuntimeError:
                pass

    @staticmethod
    def _offer(q: asyncio.Queue, payload: str) -> None:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass


hub = Hub()


def emit(kind: str, **fields: Any) -> None:
    hub.publish({"type": kind, "time": now_iso(), **fields})


# ---------------------------------------------------------------------------
# console ring buffer
# ---------------------------------------------------------------------------

LOG_RING: deque = deque(maxlen=1500)


def add_log(device: Optional[str], text: str, level: str = "info") -> None:
    entry = {"time": now_iso(), "device": device, "text": text, "level": level}
    LOG_RING.append(entry)
    emit("log", entry=entry)


def log_from_scpi(device: str) -> Callable[[str], None]:
    """Turn smu_core's '> CMD' / '< ANSWER' trace into console entries."""

    def handler(line: str) -> None:
        text = str(line)
        if text.startswith("> "):
            add_log(device, text[2:], "tx")
        elif text.startswith("< "):
            add_log(device, text[2:], "rx")
        else:
            add_log(device, text, "info")

    return handler


# ---------------------------------------------------------------------------
# devices
# ---------------------------------------------------------------------------

class Device:
    def __init__(self, letter: str) -> None:
        self.letter = letter
        self.smu = smu.B2901B(log=log_from_scpi(letter))
        self.address = ""
        self.transport = "socket"
        self.port = smu.DEFAULT_PORT
        self.timeout_ms = 10_000
        self.idn = ""
        self.output = False
        self.last: Optional[dict] = None
        self.stats = Stats()

    @property
    def connected(self) -> bool:
        return self.smu.connected

    def snapshot(self) -> dict:
        return {
            "letter": self.letter,
            "connected": self.connected,
            "address": self.address,
            "transport": self.transport,
            "port": self.port,
            "timeout_ms": self.timeout_ms,
            "idn": self.idn,
            "output": self.output,
            "four_wire": SETTINGS["four_wire"],
            "wire": smu.wire_label(SETTINGS["four_wire"]),
            "last": self.last,
            "stats": self.stats.snapshot(),
        }

    def record(self, volts: float, amps: float, ts: float, iso: str,
               persist: bool = True) -> dict:
        m = metrics(volts, amps)
        self.last = {"time": iso, **m}
        self.stats.add(m)
        if persist and SESSION is not None:
            SESSION.add_measurement(self.letter, m, ts, iso)
        return m


DEVICES: dict[str, Device] = {"A": Device("A"), "B": Device("B")}
SETTINGS: dict[str, Any] = {
    "source": "VOLT",
    "level": 0.0,
    "compliance": 1e-3,
    "auto_range": True,
    "four_wire": False,
    "sync_mode": "trigger",
}
SESSION: Optional[Session] = None
STREAM = {"running": False, "interval_ms": 500, "source": "timer"}
JOB: dict[str, Any] = {"state": "idle"}


def settings_public() -> dict:
    """SETTINGS plus the explicit 2W/4W sensing label the UI selects on."""
    out = dict(SETTINGS)
    out["wire"] = smu.wire_label(SETTINGS["four_wire"])
    return out


def resolve_wire(wire: Optional[str], four_wire: Optional[bool]) -> Optional[bool]:
    """Turn a request's ``wire``/``four_wire`` into a bool, or None if unset."""
    if wire:
        try:
            return smu.norm_wire(wire)
        except smu.SmuError as exc:
            raise HTTPException(400, str(exc)) from exc
    return four_wire



def targets() -> list[Device]:
    return [d for d in DEVICES.values() if d.connected]


def require_targets() -> list[Device]:
    out = targets()
    if not out:
        raise HTTPException(409, "No instrument connected")
    return out


def group_of(devs: list[Device], mode: Optional[str] = None) -> "smu.SyncGroup":
    return smu.SyncGroup([d.smu for d in devs], mode=mode or SETTINGS["sync_mode"],
                         log=lambda s: add_log(None, s, "info"))


def apply_settings(devs: list[Device]) -> None:
    """Program source function, level, compliance, ranges and 2W/4W sensing."""
    for dev in devs:
        dev.smu.configure(SETTINGS["source"], SETTINGS["level"],
                          SETTINGS["compliance"],
                          auto_range=SETTINGS["auto_range"],
                          wire=SETTINGS["four_wire"])
        dev.smu.check()


def all_outputs(devs: list[Device], on: bool) -> None:
    for dev in devs:
        dev.smu.output(on)
        dev.output = on
        emit("device", device=dev.snapshot())


def outputs_off(reason: str) -> None:
    for dev in DEVICES.values():
        if dev.connected:
            try:
                dev.smu.output(False, verify=False)
            except smu.SmuError:
                pass
            dev.output = False
            emit("device", device=dev.snapshot())
    add_log(None, f"all outputs OFF ({reason})", "info")


def job_public() -> dict:
    return {k: v for k, v in JOB.items() if not k.startswith("_")}


def build_state() -> dict:
    return {
        "devices": {k: d.snapshot() for k, d in DEVICES.items()},
        "settings": settings_public(),
        "stream": dict(STREAM),
        "job": job_public(),
        "session": SESSION.meta() if SESSION else None,
        "server": {"started": APP_STARTED, "data_dir": str(DATA_DIR)},
    }


# ---------------------------------------------------------------------------
# app + models
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global SESSION
    hub.attach()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION is None:
        SESSION = Session()
    add_log(None, f"server ready -- session {SESSION.id}", "info")
    yield
    stream_worker.stop_now()
    if job_running():
        JOB["_stop"].set()
    outputs_off("server shutdown")
    if SESSION:
        SESSION.event("server stopped")


app = FastAPI(title="Keysight Connect", version="2.0.0", lifespan=lifespan)



class ConnectRequest(BaseModel):
    address: str = ""
    transport: str = "socket"
    port: int = Field(default=smu.DEFAULT_PORT, ge=1, le=65535)
    timeout_ms: int = Field(default=10_000, ge=100, le=120_000)


class OutputRequest(BaseModel):
    on: bool


class ScpiRequest(BaseModel):
    command: str


class ScanRequest(BaseModel):
    ip: str
    port: int = smu.DEFAULT_PORT


class ConfigureRequest(BaseModel):
    source: str = "VOLT"
    level: float = 0.0
    compliance: float = 1e-3
    auto_range: bool = True
    four_wire: Optional[bool] = None
    wire: Optional[str] = None          # "2W" | "4W" (overrides four_wire)
    sync_mode: Optional[str] = None


class StreamRequest(BaseModel):
    interval_ms: int = Field(default=500, ge=50, le=60_000)
    log_csv: bool = True


class SweepRequest(BaseModel):
    source: str = "VOLT"
    start: float = 0.0
    stop: float = 1.0
    points: int = Field(default=41, ge=2, le=10_000)
    delay_ms: float = Field(default=20.0, ge=0.0, le=60_000)
    compliance: float = 1e-3
    auto_range: bool = True
    output_off: bool = True
    four_wire: Optional[bool] = None
    wire: Optional[str] = None          # "2W" | "4W", applies to this run
    sync_mode: Optional[str] = None


class PulseRequest(BaseModel):
    source: str = "VOLT"
    base: float = 0.0
    peak: float = 1.0
    width_ms: float = Field(default=1.0, gt=0.0, le=10_000)
    period_ms: float = Field(default=10.0, gt=0.0, le=60_000)
    delay_ms: float = Field(default=0.5, ge=0.0, le=60_000)
    aperture_ms: float = Field(default=0.1, gt=0.0, le=10_000)
    compliance: float = 1e-3
    count: int = Field(default=100, ge=1, le=100_000)
    output_off: bool = True
    four_wire: Optional[bool] = None
    wire: Optional[str] = None          # "2W" | "4W", applies to this run


# ---------------------------------------------------------------------------
# websocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = hub.register()
    try:
        await websocket.send_text(
            json.dumps({"type": "hello", "state": build_state()}, default=str))
        while True:
            payload = await queue.get()
            await websocket.send_text(payload)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unregister(queue)


# ---------------------------------------------------------------------------
# state / devices
# ---------------------------------------------------------------------------

@app.get("/api/state")
def get_state() -> dict:
    return build_state()


@app.post("/api/devices/{letter}/connect")
def connect_device(letter: str, req: ConnectRequest) -> dict:
    dev = DEVICES.get(letter.upper())
    if dev is None:
        raise HTTPException(404, f"Unknown device {letter}")
    with IO_LOCK:
        try:
            address = smu.build_address(req.address, req.port, req.transport)
            dev.smu.open(address, timeout_ms=req.timeout_ms)
        except smu.SmuError as exc:
            add_log(letter, str(exc), "err")
            raise HTTPException(400, str(exc)) from exc
        dev.address = req.address
        dev.transport = req.transport
        dev.port = req.port
        dev.timeout_ms = req.timeout_ms
        dev.idn = dev.smu.idn
        dev.output = False
        dev.last = None
        dev.stats.reset()
        # bring a freshly connected unit onto the bench's source/limit/wiring
        try:
            dev.smu.configure(SETTINGS["source"], SETTINGS["level"],
                              SETTINGS["compliance"],
                              auto_range=SETTINGS["auto_range"],
                              wire=SETTINGS["four_wire"])
        except smu.SmuError as exc:
            add_log(letter, f"settings not applied: {exc}", "err")
    add_log(letter, f"connected to {dev.idn}", "info")
    if SESSION:
        SESSION.event(f"connect {letter} {dev.idn} @ {dev.address}")
    snap = dev.snapshot()
    emit("device", device=snap)
    return snap


@app.post("/api/devices/{letter}/disconnect")
def disconnect_device(letter: str) -> dict:
    dev = DEVICES.get(letter.upper())
    if dev is None:
        raise HTTPException(404, f"Unknown device {letter}")
    with IO_LOCK:
        dev.smu.safe_shutdown()
        dev.output = False
        dev.idn = ""
        dev.last = None
    if SESSION:
        SESSION.event(f"disconnect {letter}")
    snap = dev.snapshot()
    emit("device", device=snap)
    return snap


@app.post("/api/devices/{letter}/output")
def set_output(letter: str, req: OutputRequest) -> dict:
    dev = DEVICES.get(letter.upper())
    if dev is None or not dev.connected:
        raise HTTPException(409, "Device not connected")
    with IO_LOCK:
        try:
            state = dev.smu.output(req.on)
        except smu.SmuError as exc:
            raise HTTPException(400, str(exc)) from exc
        dev.output = state
    add_log(letter, f"output {'ON' if state else 'OFF'}", "info")
    snap = dev.snapshot()
    emit("device", device=snap)
    return snap


@app.post("/api/outputs")
def set_all_outputs(req: OutputRequest) -> dict:
    with IO_LOCK:
        all_outputs(require_targets(), req.on)
    add_log(None, f"outputs {'ON' if req.on else 'OFF'}", "info")
    return build_state()


@app.post("/api/devices/{letter}/scpi")
def send_scpi(letter: str, req: ScpiRequest) -> dict:
    dev = DEVICES.get(letter.upper())
    if dev is None or not dev.connected:
        raise HTTPException(409, "Device not connected")
    cmd = req.command.strip()
    if not cmd:
        raise HTTPException(400, "Empty command")
    with IO_LOCK:
        try:
            answer = dev.smu.query(cmd) if cmd.endswith("?") else (
                dev.smu.write(cmd) or "")
            errors = dev.smu.errors()
        except smu.SmuError as exc:
            raise HTTPException(400, str(exc)) from exc
        if re.search(r"^:?OUTP", cmd, re.IGNORECASE):
            try:
                dev.output = dev.smu.output_state()
            except smu.SmuError:
                pass
    return {"response": answer, "errors": errors,
            "device": dev.snapshot()}


@app.post("/api/scan")
def scan(req: ScanRequest) -> dict:
    try:
        found = smu.scan_lan(req.ip, req.port,
                             log=lambda s: add_log(None, s, "info"))
    except smu.SmuError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"found": found}


# ---------------------------------------------------------------------------
# configure / measure / stream
# ---------------------------------------------------------------------------

@app.post("/api/configure")
def configure(req: ConfigureRequest) -> dict:
    devs = require_targets()
    wire = resolve_wire(req.wire, req.four_wire)
    SETTINGS.update({
        "source": req.source, "level": req.level,
        "compliance": req.compliance, "auto_range": req.auto_range,
    })
    if wire is not None:
        SETTINGS["four_wire"] = wire
    if req.sync_mode:
        if req.sync_mode not in smu.SyncGroup.MODES:
            raise HTTPException(400, f"Unknown sync mode {req.sync_mode}")
        SETTINGS["sync_mode"] = req.sync_mode
    with IO_LOCK:
        try:
            apply_settings(devs)
        except smu.SmuError as exc:
            # surface the wiring that is actually in effect, not the request
            try:
                SETTINGS["four_wire"] = (devs[0].smu.wire_state() == "4W")
            except smu.SmuError:
                pass
            emit("settings", settings=settings_public())
            raise HTTPException(400, str(exc)) from exc
    add_log(None, f"configured {req.source} {req.level:g} "
                 f"limit {req.compliance:g} {SETTINGS['four_wire'] and '4-wire' or '2-wire'} "
                 f"on {''.join(d.letter for d in devs)}",
            "info")
    emit("settings", settings=settings_public())
    return build_state()


def measure_once_dict(devs: list[Device]) -> tuple[list[dict], float]:
    """One V/I pair per device, BUS-synchronised when two are connected."""
    ts = time.time()
    iso = now_iso()
    if len(devs) == 1:
        d = devs[0]
        v, i = d.smu.measure_both()
        return [d.record(v, i, ts, iso)], 0.0
    group = group_of(devs)
    pairs, skew = group.measure_both_synced()
    out = [d.record(v, i, ts, iso) for d, (v, i) in zip(devs, pairs)]
    if group.fell_back:
        SETTINGS["sync_mode"] = group.mode
    return out, skew


@app.post("/api/measure")
def measure() -> dict:
    devs = require_targets()
    with IO_LOCK:
        try:
            readings, skew = measure_once_dict(devs)
        except smu.SmuError as exc:
            raise HTTPException(400, str(exc)) from exc
    payload = {
        "readings": [{"device": d.letter, "time": now_iso(),
                      "stats": d.stats.snapshot(), **m}
                     for d, m in zip(devs, readings)],
        "skew_ms": skew * 1e3,
    }
    emit("reading", **payload)
    return payload


class StreamWorker:
    """Background polling loop for the live readouts."""

    def __init__(self) -> None:
        self.thread: Optional[threading.Thread] = None
        self.stop = threading.Event()

    def start(self, interval_ms: int) -> None:
        self.stop.clear()
        STREAM.update({"running": True, "interval_ms": interval_ms})
        self.thread = threading.Thread(target=self._loop,
                                       args=(interval_ms / 1000.0,),
                                       daemon=True, name="stream")
        self.thread.start()
        add_log(None, f"continuous measurement started ({interval_ms} ms)", "info")
        emit("stream", stream=dict(STREAM))

    def _loop(self, period: float) -> None:
        devs = targets()
        while not self.stop.wait(0):
            t0 = time.perf_counter()
            with IO_LOCK:
                if JOB.get("state") == "running":
                    pass
                else:
                    try:
                        readings, skew = measure_once_dict(devs)
                    except (smu.SmuError, HTTPException) as exc:
                        add_log(None, f"stream error: {exc}", "err")
                        break
                    emit("reading",
                         readings=[{"device": d.letter, "time": now_iso(),
                                    "stats": d.stats.snapshot(), **m}
                                   for d, m in zip(devs, readings)],
                         skew_ms=skew * 1e3)
            delay = period - (time.perf_counter() - t0)
            if self.stop.wait(max(0.0, delay)):
                break
        STREAM["running"] = False
        emit("stream", stream=dict(STREAM))
        add_log(None, "continuous measurement stopped", "info")

    def stop_now(self) -> None:
        self.stop.set()
        STREAM["running"] = False
        emit("stream", stream=dict(STREAM))


stream_worker = StreamWorker()


@app.post("/api/stream/start")
def stream_start(req: StreamRequest) -> dict:
    devs = require_targets()
    if JOB.get("state") == "running":
        raise HTTPException(409, "A sweep or pulse run is in progress")
    if STREAM["running"]:
        stream_worker.stop_now()
        time.sleep(0.05)
    with IO_LOCK:
        try:
            for dev in devs:
                if not dev.output:
                    dev.smu.output(True)
                    dev.output = True
                    emit("device", device=dev.snapshot())
        except smu.SmuError as exc:
            raise HTTPException(400, str(exc)) from exc
    stream_worker.start(req.interval_ms)
    return build_state()


@app.post("/api/stream/stop")
def stream_stop() -> dict:
    stream_worker.stop_now()
    return build_state()


# ---------------------------------------------------------------------------
# jobs: sweeps and pulse trains
# ---------------------------------------------------------------------------

def job_running() -> bool:
    return JOB.get("state") == "running"


def start_job(kind: str, params: dict, runner: Callable[..., None]) -> dict:
    if job_running():
        raise HTTPException(409, f"{JOB.get('kind')} already running")
    if STREAM["running"]:
        stream_worker.stop_now()
        time.sleep(0.05)
    job = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "state": "running",
        "progress": 0,
        "total": params.get("points") or params.get("count") or 0,
        "started": now_iso(),
        "params": params,
        "message": "",
    }
    JOB.clear()
    JOB.update(job)
    stop = threading.Event()
    JOB["_stop"] = stop

    def wrapper() -> None:
        try:
            runner(stop)
            JOB["state"] = "done"
        except Exception as exc:                       # noqa: BLE001
            JOB["state"] = "error"
            JOB["message"] = str(exc)
            add_log(None, f"{kind} failed: {exc}", "err")
            emit("job", job=job_public())
        finally:
            if JOB.get("state") == "running":
                JOB["state"] = "done"
            emit("job", job=job_public())
            if SESSION:
                SESSION.event(f"{kind} {JOB['state']} ({JOB.get('id')})")

    threading.Thread(target=wrapper, daemon=True, name=kind).start()
    emit("job", job=job_public())
    return {"job": job_public()}


@app.post("/api/jobs/stop")
def job_stop() -> dict:
    if not job_running():
        return {"job": None}
    JOB["_stop"].set()
    add_log(None, f"stopping {JOB.get('kind')} ...", "info")
    return {"job": job_public()}


def run_sweep(stop: threading.Event) -> None:
    p = JOB["params"]
    devs = targets()
    if not devs:
        raise RuntimeError("No instrument connected")
    src = "VOLT" if str(p["source"]).upper().startswith("V") else "CURR"
    meas = "CURR" if src == "VOLT" else "VOLT"
    run_id = JOB["id"]

    with IO_LOCK:
        for dev in devs:
            dev.smu.configure(src, p["start"], p["compliance"],
                              auto_range=p["auto_range"],
                              wire=p.get("four_wire", SETTINGS["four_wire"]))
        all_outputs(devs, True)

    series: dict[str, list[dict]] = {d.letter: [] for d in devs}
    emit("sweep_start", run_id=run_id, source=src, measure=meas,
         devices=[d.letter for d in devs], params=p)

    def one_point(i: int, x: float, values: list[float]) -> None:
        iso = now_iso()
        rows = []
        for dev, val in zip(devs, values):
            v, i_ = (x, val) if src == "VOLT" else (val, x)
            m = metrics(v, i_)
            dev.last = {"time": iso, **m}
            dev.stats.add(m)
            if SESSION:
                SESSION.add_sweep_point(run_id, i, dev.letter, x, m, iso)
            row = {"device": dev.letter, "index": i, "x": x, **m}
            series[dev.letter].append(row)
            rows.append(row)
        JOB["progress"] = i + 1
        JOB["x"] = x
        emit("sweep_point", run_id=run_id, index=i, x=x, rows=rows,
             progress=i + 1, total=JOB["total"])

    try:
        with IO_LOCK:
            if len(devs) == 1:
                devs[0].smu.sweep(
                    src, p["start"], p["stop"], p["points"],
                    delay_s=p["delay_ms"] / 1000.0,
                    on_point=lambda i, x, y: one_point(i, x, [y]),
                    should_stop=stop.is_set)
            else:
                group = group_of(devs, p.get("sync_mode"))
                group.sweep_synced(
                    src, p["start"], p["stop"], p["points"],
                    delay_s=p["delay_ms"] / 1000.0,
                    on_point=one_point, should_stop=stop.is_set)
                if group.fell_back:
                    SETTINGS["sync_mode"] = group.mode
                    emit("settings", settings=settings_public())
    finally:
        with IO_LOCK:
            if p.get("output_off", True):
                all_outputs(devs, False)
            else:
                try:
                    for dev in devs:
                        dev.smu.write(f":SOURce:{src} {p['start']:.9g}")
                except smu.SmuError:
                    pass
        emit("sweep_done", run_id=run_id, aborted=stop.is_set(),
             series=series,
             output_off=bool(p.get("output_off", True)))
        if SESSION:
            SESSION.event(f"sweep {run_id} finished "
                          f"({JOB['progress']}/{JOB['total']})")


@app.post("/api/jobs/sweep")
def start_sweep(req: SweepRequest) -> dict:
    require_targets()
    wire = resolve_wire(req.wire, req.four_wire)
    if wire is not None:
        SETTINGS["four_wire"] = wire
        emit("settings", settings=settings_public())
    params = req.model_dump()
    params["four_wire"] = SETTINGS["four_wire"]
    res = start_job("sweep", params, run_sweep)
    add_log(None, f"sweep starting in {SETTINGS['four_wire'] and '4-wire' or '2-wire'}",
            "info")
    if SESSION:
        SESSION.event(f"sweep start {params}")
    return res


def run_pulse(stop: threading.Event) -> None:
    p = JOB["params"]
    devs = targets()
    if not devs:
        raise RuntimeError("No instrument connected")
    src = "VOLT" if str(p["source"]).upper().startswith("V") else "CURR"
    meas = "CURR" if src == "VOLT" else "VOLT"
    run_id = JOB["id"]
    count = int(p["count"])
    period = p["period_ms"] / 1000.0

    with IO_LOCK:
        for dev in devs:
            dev.smu.pulse_config(
                src, p["base"], p["peak"], p["width_ms"] / 1000.0,
                p["period_ms"] / 1000.0, p["delay_ms"] / 1000.0,
                p["aperture_ms"] / 1000.0, p["compliance"], count,
                wire=p.get("four_wire", SETTINGS["four_wire"]))
        all_outputs(devs, True)
        stamps = []
        for dev in devs:
            stamps.append(time.perf_counter())
            dev.smu.pulse_start()
    skew = (max(stamps) - min(stamps)) if len(stamps) > 1 else 0.0

    emit("pulse_start", run_id=run_id, source=src, measure=meas,
         devices=[d.letter for d in devs], params=p, skew_ms=skew * 1e3)

    samples: dict[str, list[float]] = {d.letter: [] for d in devs}
    deadline = time.perf_counter() + count * period + 0.4
    try:
        while time.perf_counter() < deadline and not stop.is_set():
            for dev in devs:
                try:
                    with IO_LOCK:
                        values = dev.smu.pulse_fetch(meas)
                except smu.SmuError:
                    values = []
                new = values[len(samples[dev.letter]):]
                for offset, val in enumerate(new):
                    index = len(samples[dev.letter])
                    samples[dev.letter].append(val)
                    v, i_ = ((p["peak"], val) if src == "VOLT"
                             else (val, p["peak"]))
                    m = metrics(v, i_)
                    iso = now_iso()
                    if SESSION:
                        SESSION.add_pulse_point(run_id, dev.letter, index,
                                                p["peak"], m, iso)
                    emit("pulse_point", run_id=run_id, device=dev.letter,
                         index=index, measured=val, **m)
                JOB["progress"] = max(len(v) for v in samples.values())
                emit("job", job=job_public())
            time.sleep(0.08)
    finally:
        with IO_LOCK:
            for dev in devs:
                try:
                    dev.smu.abort()
                except smu.SmuError:
                    pass
            for dev in devs:
                try:
                    values = dev.smu.pulse_fetch(meas)
                except smu.SmuError:
                    values = []
                samples[dev.letter] = values
            for dev in devs:
                try:
                    dev.smu.write(":SOURce:FUNCtion:SHAPe DC")
                    dev.smu.restore_auto_trigger()
                except smu.SmuError:
                    pass
            if p.get("output_off", True):
                all_outputs(devs, False)

    series = {}
    for dev in devs:
        values = samples[dev.letter]
        finite = [v for v in values if v is not None]
        stats = {
            "n": len(values),
            "mean": (sum(finite) / len(finite)) if finite else None,
            "min": min(finite) if finite else None,
            "max": max(finite) if finite else None,
        }
        if len(finite) > 1 and stats["mean"] is not None:
            var = sum((v - stats["mean"]) ** 2 for v in finite) / (len(finite) - 1)
            stats["std"] = var ** 0.5
        else:
            stats["std"] = None
        rows = []
        for index, val in enumerate(values):
            v, i_ = ((p["peak"], val) if src == "VOLT" else (val, p["peak"]))
            rows.append({"index": index, "measured": val, **metrics(v, i_)})
        series[dev.letter] = {"values": values, "stats": stats, "rows": rows}

    emit("pulse_done", run_id=run_id, aborted=stop.is_set(), series=series,
         skew_ms=skew * 1e3, output_off=bool(p.get("output_off", True)))
    if SESSION:
        SESSION.event(f"pulse {run_id} finished ({len(samples[devs[0].letter])} samples)")


@app.post("/api/jobs/pulse")
def start_pulse(req: PulseRequest) -> dict:
    require_targets()
    if req.aperture_ms > req.width_ms:
        raise HTTPException(400, "Aperture must not be wider than the pulse")
    if req.delay_ms + req.width_ms >= req.period_ms:
        raise HTTPException(400, "Pulse delay + width must fit inside the period")
    wire = resolve_wire(req.wire, req.four_wire)
    if wire is not None:
        SETTINGS["four_wire"] = wire
        emit("settings", settings=settings_public())
    params = req.model_dump()
    params["four_wire"] = SETTINGS["four_wire"]
    res = start_job("pulse", params, run_pulse)
    add_log(None, f"pulse train starting in "
                  f"{SETTINGS['four_wire'] and '4-wire' or '2-wire'}", "info")
    if SESSION:
        SESSION.event(f"pulse start {params}")
    return res


# ---------------------------------------------------------------------------
# console log
# ---------------------------------------------------------------------------

@app.get("/api/log")
def get_log(limit: int = 400) -> dict:
    return {"entries": list(LOG_RING)[-limit:]}


@app.post("/api/panic")
def panic() -> dict:
    stream_worker.stop_now()
    if job_running():
        JOB["_stop"].set()
    outputs_off("panic / outputs off")
    if SESSION:
        SESSION.event("PANIC: outputs off")
    return build_state()


# ---------------------------------------------------------------------------
# session / exports
# ---------------------------------------------------------------------------

@app.get("/api/session")
def session_info() -> dict:
    if SESSION is None:
        raise HTTPException(404, "No session")
    return SESSION.meta()


@app.post("/api/session/new")
def session_new(name: str = "") -> dict:
    global SESSION
    if SESSION:
        SESSION.save_meta()
        SESSION.event("session closed")
    SESSION = Session(name)
    add_log(None, f"new session {SESSION.id}", "info")
    emit("session", session=SESSION.meta())
    return SESSION.meta()


@app.get("/api/session/export/{kind}")
def session_export(kind: str) -> Response:
    if SESSION is None:
        raise HTTPException(404, "No session")
    paths = {"measurements": SESSION.meas_path, "sweeps": SESSION.sweep_path,
             "pulses": SESSION.pulse_path, "events": SESSION.events_path,
             "meta": SESSION.dir / "meta.json"}
    if kind == "all":
        data = SESSION.zip_bytes()
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{SESSION.id}.zip"'})
    path = paths.get(kind)
    if path is None or not path.exists():
        raise HTTPException(404, f"Unknown export {kind}")
    media = "application/json" if path.suffix == ".json" else "text/csv"
    return FileResponse(path, media_type=media, filename=path.name)


# ---------------------------------------------------------------------------
# static front-end (built by `npm run build`)
# ---------------------------------------------------------------------------

if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> Response:
    if not DIST.exists():
        return Response(
            "<h1>Keysight Connect</h1><p>Front-end not built yet. Run "
            "<code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>, "
            "or use the Vite dev server on port 5173.</p>",
            media_type="text/html")
    candidate = (DIST / path).resolve()
    if path and candidate.is_file() and str(candidate).startswith(str(DIST)):
        return FileResponse(candidate)
    return FileResponse(DIST / "index.html")


def main() -> None:
    parser = argparse.ArgumentParser(description="Keysight Connect web backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run("server.app:app", host=args.host, port=args.port,
                reload=args.reload)
    # uvicorn returns on SIGTERM, but a third-party non-daemon thread (the
    # pyvisa-py socket backend) can keep the interpreter alive afterwards.
    os._exit(0)


if __name__ == "__main__":
    main()

