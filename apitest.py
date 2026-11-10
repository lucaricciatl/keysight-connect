#!/usr/bin/env python3
"""apitest.py -- end-to-end test of the web backend against mock instruments.

Starts two fake B2901B units (100 ohm and 200 ohm loads), boots the FastAPI
app on a spare port, and exercises every endpoint the React UI uses:
connect, configure, measure, continuous streaming, IV sweep, pulse train,
4-wire sense, SCPI console, panic and the session exports.

    python3 apitest.py
    exit code 0 == all checks passed
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

import mock_b2901b

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"{mark}  {name}" + (f"   ({detail})" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def close(a: float | None, b: float, rel: float = 2e-3, abs_: float = 1e-9) -> bool:
    if a is None:
        return False
    return abs(a - b) <= max(abs_, rel * abs(b))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_mock(port: int, load: float) -> None:
    srv = mock_b2901b.serve(port, verbose=False, load_ohms=load)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None,
             raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
                return payload if raw else json.loads(payload or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc

    def get(self, path: str, raw: bool = False):
        return self.call("GET", path, raw=raw)

    def post(self, path: str, body: dict | None = None):
        return self.call("POST", path, body if body is not None else {})

    def status_for(self, path: str, body: dict | None = None) -> int:
        """HTTP status of a POST, without raising on 4xx (for negative tests)."""
        try:
            self.post(path, body)
            return 200
        except RuntimeError as exc:
            text = str(exc)
            head = text.split("->", 1)[-1].strip().split(":", 1)[0].strip()
            try:
                return int(head)
            except ValueError:
                return -1


def wait_ready(api: Api, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            api.get("/api/state")
            return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("server did not become ready")


def main() -> int:
    port_a, port_b, port_api = free_port(), free_port(), free_port()
    start_mock(port_a, 100.0)
    start_mock(port_b, 200.0)

    import server.app as backend
    import uvicorn

    config = uvicorn.Config(backend.app, host="127.0.0.1", port=port_api,
                            log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    api = Api(f"http://127.0.0.1:{port_api}")
    try:
        wait_ready(api)

        # ---- connect both units -----------------------------------------
        a = api.post("/api/devices/A/connect", {"address": "127.0.0.1",
                                                "port": port_a,
                                                "timeout_ms": 5000})
        b = api.post("/api/devices/B/connect", {"address": "127.0.0.1",
                                                "port": port_b,
                                                "timeout_ms": 5000})
        check("connect A identifies a B2901B", "B2901B" in a["idn"], a["idn"][:40])
        check("connect B identifies a B2901B", "B2901B" in b["idn"], b["idn"][:40])

        # ---- configure + four-wire --------------------------------------
        state = api.post("/api/configure", {"source": "VOLT", "level": 0.5,
                                            "compliance": 0.02,
                                            "auto_range": True,
                                            "four_wire": True})
        check("configure echoes settings", state["settings"]["level"] == 0.5,
              str(state["settings"]))
        remote = api.post("/api/devices/A/scpi",
                          {"command": ":SENS:REM?"})["response"]
        check("4-wire sense programmed", remote.strip() == "1", remote)
        check("state reports 4W", state["settings"]["wire"] == "4W",
              state["settings"]["wire"])
        check("device snapshot reports 4W",
              state["devices"]["A"]["wire"] == "4W"
              and state["devices"]["B"]["wire"] == "4W",
              f"{state['devices']['A']['wire']}/{state['devices']['B']['wire']}")

        # switch back to 2-wire with the explicit selector
        state = api.post("/api/configure", {"source": "VOLT", "level": 0.5,
                                            "compliance": 0.02,
                                            "auto_range": True, "wire": "2W"})
        remote = api.post("/api/devices/A/scpi",
                          {"command": ":SENS:REM?"})["response"]
        check("2-wire selected via wire=2W",
              remote.strip() == "0" and state["settings"]["wire"] == "2W",
              f"{remote.strip()} / {state['settings']['wire']}")
        bad = api.status_for("/api/configure", {"source": "VOLT", "level": 0.5,
                                                "compliance": 0.02,
                                                "wire": "3W"})
        check("rejects an unknown wire mode with 400", bad == 400, str(bad))

        # ---- output + single measurement --------------------------------
        api.post("/api/devices/A/output", {"on": True})
        api.post("/api/devices/B/output", {"on": True})
        m = api.post("/api/measure")
        ra, rb = m["readings"]
        check("measure A: 0.5 V, 5 mA", close(ra["volts"], 0.5)
              and close(ra["amps"], 0.005), f"{ra['volts']} V {ra['amps']} A")
        check("measure B: 0.5 V, 2.5 mA", close(rb["volts"], 0.5)
              and close(rb["amps"], 0.0025), f"{rb['volts']} V {rb['amps']} A")
        check("resistance derived (100 / 200 ohm)",
              close(ra["ohms"], 100.0) and close(rb["ohms"], 200.0),
              f"{ra['ohms']:.1f} / {rb['ohms']:.1f}")
        check("power derived (2.5 mW / 1.25 mW)",
              close(ra["watts"], 0.0025) and close(rb["watts"], 0.00125),
              f"{ra['watts']:.3e} / {rb['watts']:.3e}")
        check("BUS trigger sync reported", m["skew_ms"] >= 0.0,
              f"{m['skew_ms']:.3f} ms")

        # ---- continuous streaming --------------------------------------
        before = api.get("/api/session")["counts"]["measurements"]
        api.post("/api/stream/start", {"interval_ms": 100})
        time.sleep(0.6)
        api.post("/api/stream/stop")
        after = api.get("/api/session")["counts"]["measurements"]
        check("streaming logged samples", after - before >= 3,
              f"{after - before} rows")

        # ---- IV sweep ---------------------------------------------------
        started = api.post("/api/jobs/sweep", {
            "source": "VOLT", "start": 0.0, "stop": 1.0, "points": 11,
            "delay_ms": 0, "compliance": 0.02, "auto_range": True,
            "output_off": True,
        })
        run_id = started["job"]["id"]
        deadline = time.time() + 20
        while time.time() < deadline:
            job = api.get("/api/state")["job"]
            if job.get("state") != "running":
                break
            time.sleep(0.05)
        check("sweep job finished", job.get("state") == "done", str(job.get("state")))
        check("sweep progress reached 11", job.get("progress") == 11,
              str(job.get("progress")))

        rows = api.get("/api/session/export/sweeps", raw=True).decode().strip().splitlines()
        data_rows = [r for r in rows[1:] if r]
        check("sweep CSV has 22 rows (11 points x 2 units)",
              len(data_rows) == 22, f"{len(data_rows)} rows")
        last_a = [r for r in data_rows if f",A," in r][-1].split(",")
        check("sweep end point A = 1 V, 10 mA",
              close(float(last_a[5]), 1.0) and close(float(last_a[6]), 0.01),
              f"{last_a[5]} V {last_a[6]} A")
        check("sweep outputs switched off afterwards",
              api.get("/api/state")["devices"]["A"]["output"] is False)

        # ---- pulse train -------------------------------------------------
        api.post("/api/devices/A/output", {"on": True})
        api.post("/api/devices/B/output", {"on": True})
        api.post("/api/jobs/pulse", {
            "source": "VOLT", "base": 0.0, "peak": 0.5,
            "width_ms": 1.0, "period_ms": 5.0, "delay_ms": 0.2,
            "aperture_ms": 0.1, "compliance": 0.02, "count": 20,
            "output_off": True,
        })
        deadline = time.time() + 15
        while time.time() < deadline:
            job = api.get("/api/state")["job"]
            if job.get("state") != "running":
                break
            time.sleep(0.05)
        check("pulse job finished", job.get("state") == "done", str(job.get("state")))
        check("pulse counted 20 samples x 2 units", job.get("progress") == 20,
              str(job.get("progress")))
        prow = api.get("/api/session/export/pulses", raw=True).decode().strip().splitlines()
        pdata = [r for r in prow[1:] if r]
        check("pulse CSV has 40 rows", len(pdata) == 40, f"{len(pdata)} rows")
        pa = [r for r in pdata if r.split(",")[2] == "A"]
        check("pulse samples read 5 mA on A", len(pa) == 20
              and close(float(pa[-1].split(",")[6]), 0.005),
              f"{len(pa)} rows, last {pa[-1].split(',')[6] if pa else '-'}")

        # ---- SCPI console ------------------------------------------------
        idn = api.post("/api/devices/A/scpi", {"command": "*IDN?"})
        check("SCPI console answers queries", "B2901B" in idn["response"],
              idn["response"][:40])
        api.post("/api/devices/A/scpi", {"command": ":OUTP ON"})
        check("SCPI console reflects output state",
              api.get("/api/state")["devices"]["A"]["output"] is True)

        # ---- panic --------------------------------------------------------
        api.post("/api/panic")
        check("panic switches outputs off",
              api.get("/api/state")["devices"]["A"]["output"] is False)

        # ---- exports -------------------------------------------------------
        meas = api.get("/api/session/export/measurements", raw=True)
        check("measurements CSV downloadable", b"timestamp" in meas[:200]
              and len(meas) > 200, f"{len(meas)} bytes")
        zip_bytes = api.get("/api/session/export/all", raw=True)
        check("session ZIP downloadable", zip_bytes[:2] == b"PK",
              f"{len(zip_bytes)} bytes")

        sess = api.get("/api/session")
        check("session reports three data files",
              len([f for f in sess["files"] if f["name"].endswith(".csv")]) == 3,
              str([f["name"] for f in sess["files"]]))

        # ---- disconnect -----------------------------------------------------
        api.post("/api/devices/A/disconnect")
        api.post("/api/devices/B/disconnect")
        state = api.get("/api/state")
        check("disconnect clears both devices",
              not state["devices"]["A"]["connected"]
              and not state["devices"]["B"]["connected"])

    except Exception as exc:                              # noqa: BLE001
        traceback.print_exc()
        failures.append(f"exception: {exc}")
    finally:
        server.should_exit = True
        time.sleep(0.3)

    print()
    if failures:
        print(f"{len(failures)} FAILED: " + ", ".join(failures))
        return 1
    print("all API checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
