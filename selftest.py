#!/usr/bin/env python3
"""selftest.py -- exercise smu_core against mock B2901B instruments.

Covers a single unit, a synchronised pair (BUS trigger), and the automatic
fallback when an instrument refuses BUS triggering.

Run:  python3 selftest.py
Exit code 0 means every check passed.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import traceback

import smu_core as smu
import mock_b2901b

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


def close(a: float, b: float, rel: float = 1e-6, abs_: float = 1e-9) -> bool:
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_mock(port: int, load: float = 100.0,
               trigger: bool = True) -> mock_b2901b.Server:
    server = mock_b2901b.serve(port, load_ohms=load, support_trigger=trigger)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def addr(port: int) -> str:
    return smu.build_address("127.0.0.1", port)


# --------------------------------------------------------------------------
# single instrument
# --------------------------------------------------------------------------

def single_checks(port: int) -> None:
    inst = smu.B2901B()
    try:
        check("build_address socket", addr(port) == f"TCPIP0::127.0.0.1::{port}::SOCKET")
        check("build_address vxi11",
              smu.build_address("10.0.0.5", 5025, "vxi11")
              == "TCPIP0::10.0.0.5::inst0::INSTR")
        check("build_address passthrough",
              smu.build_address("TCPIP0::1.2.3.4::5025::SOCKET")
              == "TCPIP0::1.2.3.4::5025::SOCKET")

        idn = inst.open(addr(port), timeout_ms=4000)
        check("open + *IDN?", "B2901B" in idn, idn)
        check("backend detected", bool(inst.backend), inst.backend)

        inst.configure("VOLT", 0.5, 0.01, auto_range=True)
        check("configure clean", inst.errors() == [])
        check("output off initially", inst.output_state() is False)

        inst.output(True)
        check("output ON verified", inst.output_state() is True)
        i_meas = inst.measure("CURR")
        check("I = 0.5 V / 100 ohm = 5 mA", close(i_meas, 0.005), f"{i_meas:.9e}")
        check("V reads back 0.5 V", close(inst.measure("VOLT"), 0.5),
              f"{inst.measure('VOLT'):.9e}")

        v2, i2 = inst.measure_both()
        check(":READ? VOLT,CURR pair", close(v2, 0.5) and close(i2, 0.005),
              f"{v2:.6e}, {i2:.6e}")

        inst.configure("VOLT", 5.0, 0.01, auto_range=True)
        clipped = inst.measure("CURR")
        check("compliance clips at 10 mA", close(clipped, 0.01), f"{clipped:.9e}")

        inst.configure("VOLT", 0.0, 0.02, auto_range=True)
        seen = []
        data = inst.sweep("VOLT", 0.0, 1.0, 6, delay_s=0.005,
                          on_point=lambda i, x, y: seen.append((i, x, y)))
        expected = [0.0, 0.002, 0.004, 0.006, 0.008, 0.01]
        got = [round(y, 9) for _, y in data]
        check("sweep point count", len(data) == 6, str(len(data)))
        check("sweep linear in 100 ohm",
              all(close(g, e) for g, e in zip(got, expected)), str(got))
        check("sweep callback fired per point", len(seen) == 6, str(len(seen)))
        check("sweep x axis",
              [round(x, 6) for x, _ in data] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
              str([round(x, 6) for x, _ in data]))

        state = {"stop": False}
        data = inst.sweep(
            "VOLT", 0.0, 1.0, 50, delay_s=0.0,
            on_point=lambda i, x, y: state.__setitem__("stop", i >= 3),
            should_stop=lambda: state["stop"])
        check("sweep abort stops early", 3 < len(data) < 50, str(len(data)))

        inst.configure("CURR", 1e-3, 5.0, auto_range=True)
        check("source current, V measured", close(inst.measure("VOLT"), 0.1),
              f"{inst.measure('VOLT'):.6e}")

        inst.output(False)
        check("output OFF verified", inst.output_state() is False)
        check("measurement is zero with output off",
              close(inst.measure("CURR"), 0.0))
        check("no SCPI errors after session", inst.errors() == [])

        try:
            smu.B2901B().open(addr(free_port()), timeout_ms=1200)
        except smu.SmuError as exc:
            text = str(exc)
            check("dead port raises SmuError with hint",
                  ("TCP connection failed" in text or "Cannot open" in text)
                  and "5025" in text, text.splitlines()[-1][:90])
        else:
            check("dead port raises SmuError", False, "no exception")

        found = smu.scan_lan("127.0.0.1", port=port)
        check("scan_lan finds the mock",
              any(f["ip"] == "127.0.0.1" and "B2901B" in f["idn"] for f in found),
              str(found))

        inst.safe_shutdown()
        check("safe_shutdown closes port", not inst.connected)
    finally:
        inst.close()


# --------------------------------------------------------------------------
# synchronised pair
# --------------------------------------------------------------------------

def sync_checks(port_a: int, port_b: int, port_c: int) -> None:
    a = smu.B2901B()
    b = smu.B2901B()
    c = smu.B2901B()
    try:
        a.open(addr(port_a), timeout_ms=4000)      # load 100 ohm
        b.open(addr(port_b), timeout_ms=4000)      # load 200 ohm
        c.open(addr(port_c), timeout_ms=4000)      # load 100 ohm, no BUS trigger

        group = smu.SyncGroup([a, b], mode="trigger")
        group.configure("VOLT", 0.5, 0.02)
        states = group.output(True)
        check("sync: both outputs on", states == [True, True], str(states))
        check("sync: both output states read back True",
              group.output_states() == [True, True])

        values, skew = group.measure_synced("CURR")
        check("sync: triggered I(A)=5 mA, I(B)=2.5 mA",
              close(values[0], 0.005) and close(values[1], 0.0025),
              str(values))
        check("sync: trigger mode kept", group.mode == "trigger", group.mode)
        check("sync: skew measured and small", 0.0 <= skew < 0.2,
              f"{skew * 1e3:.3f} ms")
        check("sync: trigger source restored to AUTO afterwards",
              a.query(":TRIG:SOUR?").strip().upper() == "AUTO",
              a.query(":TRIG:SOUR?"))

        volts, _ = group.measure_synced("VOLT")
        check("sync: both measure 0.5 V",
              close(volts[0], 0.5) and close(volts[1], 0.5), str(volts))

        pairs, pair_skew = group.measure_both_synced()
        check("sync: paired V/I on A = 0.5 V, 5 mA",
              close(pairs[0][0], 0.5) and close(pairs[0][1], 0.005),
              str(pairs[0]))
        check("sync: paired V/I on B = 0.5 V, 2.5 mA",
              close(pairs[1][0], 0.5) and close(pairs[1][1], 0.0025),
              str(pairs[1]))
        check("sync: paired skew small", 0.0 <= pair_skew < 0.2,
              f"{pair_skew * 1e3:.3f} ms")

        data = group.sweep_synced("VOLT", 0.0, 1.0, 5, delay_s=0.0)
        check("sync: sweep 5 points", len(data) == 5, str(len(data)))
        check("sync: sweep end 10 mA / 5 mA",
              close(data[-1][1][0], 0.01) and close(data[-1][1][1], 0.005),
              str(data[-1]))
        check("sync: sweep x axis",
              [round(r[0], 6) for r in data] == [0.0, 0.25, 0.5, 0.75, 1.0],
              str([round(r[0], 6) for r in data]))
        check("sync: trigger mode still in use after sweep",
              group.mode == "trigger", group.mode)
        check("sync: output still on after sweep", group.output_states()
              == [True, True])

        # ---- fallback when one unit refuses BUS triggering
        group2 = smu.SyncGroup([a, c], mode="trigger")
        group2.configure("VOLT", 0.5, 0.02)
        group2.output(True)
        values, skew = group2.measure_synced("CURR")
        check("sync: fallback engaged when trigger unsupported",
              group2.mode == "parallel" and group2.fell_back, group2.mode)
        check("sync: fallback values still correct",
              close(values[0], 0.005) and close(values[1], 0.005), str(values))
        check("sync: fallback reports zero skew", skew == 0.0, str(skew))

        data = group2.sweep_synced("VOLT", 0.0, 1.0, 3, delay_s=0.0)
        check("sync: fallback sweep works", len(data) == 3
              and close(data[-1][1][0], 0.01), str(data[-1]))

        group2.output(False)
        check("sync: fallback outputs off", group2.output_states()
              == [False, False])

        # ---- sequential mode
        group3 = smu.SyncGroup([a, b], mode="sequential")
        group3.configure("VOLT", 0.5, 0.02)
        group3.output(True)
        values, _ = group3.measure_synced("CURR")
        check("sync: sequential mode values",
              close(values[0], 0.005) and close(values[1], 0.0025), str(values))
        group3.output(False)

        group.output(False)
        check("sync: everything off", group.output_states() == [False, False])
        check("sync: no errors left on A", a.errors() == [])
    finally:
        for unit in (a, b, c):
            try:
                unit.safe_shutdown()
            except Exception:
                pass


# --------------------------------------------------------------------------
# pulsed output
# --------------------------------------------------------------------------

def pulse_checks(port_a: int, port_b: int) -> None:
    a = smu.B2901B()
    b = smu.B2901B()
    try:
        a.open(addr(port_a), timeout_ms=4000)
        b.open(addr(port_b), timeout_ms=4000)

        # ---- single unit pulse train
        a.pulse_config("VOLT", base=0.0, peak=0.5, width_s=1e-3, period_s=5e-3,
                       pulse_delay_s=0.5e-3, aperture_s=1e-4,
                       compliance=0.02, count=10)
        check("pulse: configure accepted", a.errors() == [])
        check("pulse: shape is PULSe",
              a.query(":SOUR:FUNC:SHAP?").strip().upper().startswith("PULS"),
              a.query(":SOUR:FUNC:SHAP?"))

        data = a.pulse_run(10, 5e-3, "CURR")
        check("pulse: 10 results returned", len(data) == 10, str(len(data)))
        check("pulse: every sample is the peak current",
              all(abs(v - 0.005) < 1e-9 for v in data),
              f"first={data[0]:.6e} last={data[-1]:.6e}")

        a.pulse_cleanup()
        check("pulse: output off after cleanup", a.output_state() is False)
        check("pulse: shape restored to DC",
              a.query(":SOUR:FUNC:SHAP?").strip().upper() == "DC")

        # ---- current-source pulses measure voltage
        a.pulse_config("CURR", base=0.0, peak=1e-3, width_s=2e-3, period_s=10e-3,
                       pulse_delay_s=0.5e-3, aperture_s=2e-4,
                       compliance=5.0, count=4)
        volts = a.pulse_run(4, 10e-3, "VOLT")
        check("pulse: current pulses read 0.1 V",
              len(volts) == 4 and all(abs(v - 0.1) < 1e-9 for v in volts),
              str(volts))
        a.pulse_cleanup()

        # ---- parameter validation
        for name, kwargs in (
                ("aperture wider than pulse",
                 dict(width_s=0.5e-3, aperture_s=1e-3)),
                ("delay+width exceeds period",
                 dict(width_s=5e-3, period_s=5e-3, pulse_delay_s=1e-3)),
                ("zero count", dict(count=0)),
        ):
            args = dict(sour_func="VOLT", base=0.0, peak=0.5, width_s=1e-3,
                        period_s=10e-3, pulse_delay_s=0.5e-3,
                        aperture_s=1e-4, compliance=0.02, count=5)
            args.update(kwargs)
            try:
                a.pulse_config(**args)
            except smu.SmuError:
                check(f"pulse: rejects {name}", True)
            else:
                check(f"pulse: rejects {name}", False, "no exception")

        # ---- abort in the middle of a long train
        a.pulse_config("VOLT", base=0.0, peak=0.5, width_s=1e-3, period_s=10e-3,
                       pulse_delay_s=0.5e-3, aperture_s=1e-4,
                       compliance=0.02, count=200)
        t0 = time.monotonic()
        partial = a.pulse_run(200, 10e-3, "CURR",
                              should_stop=lambda: time.monotonic() - t0 > 0.3)
        check("pulse: abort returns partial data",
              0 < len(partial) < 200 and time.monotonic() - t0 < 1.5,
              f"{len(partial)} samples in {time.monotonic() - t0:.2f} s")
        a.pulse_cleanup()

        # ---- synchronised pulse train on two units
        group = smu.SyncGroup([a, b], mode="trigger")
        group.pulse_configure("VOLT", base=0.0, peak=0.5, width_s=1e-3,
                              period_s=5e-3, pulse_delay_s=0.5e-3,
                              aperture_s=1e-4, compliance=0.02, count=7)
        arrays, skew = group.pulse_train(7, 5e-3, "CURR")
        check("pulse: both units returned 7 samples",
              len(arrays[0]) == 7 and len(arrays[1]) == 7,
              str([len(x) for x in arrays]))
        check("pulse: synchronised values 5 mA / 2.5 mA",
              abs(arrays[0][0] - 0.005) < 1e-9
              and abs(arrays[1][0] - 0.0025) < 1e-9,
              f"{arrays[0][0]:.6e}, {arrays[1][0]:.6e}")
        check("pulse: init skew reported", 0.0 <= skew < 0.2,
              f"{skew * 1e3:.3f} ms")
        group.pulse_cleanup()
        check("pulse: both outputs off after sync run",
              [a.output_state(), b.output_state()] == [False, False])
        check("pulse: no errors left", a.errors() == [] and b.errors() == [])
    finally:
        for unit in (a, b):
            try:
                unit.pulse_cleanup()
                unit.safe_shutdown()
            except Exception:
                pass


# --------------------------------------------------------------------------

def main() -> int:
    ports = {"a": free_port(), "b": free_port(), "c": free_port()}
    servers = [
        start_mock(ports["a"], load=100.0, trigger=True),
        start_mock(ports["b"], load=200.0, trigger=True),
        start_mock(ports["c"], load=100.0, trigger=False),
    ]
    print("mocks: A={a} (100 ohm)  B={b} (200 ohm)  C={c} (no trigger)"
          .format(**ports))

    try:
        single_checks(ports["a"])
        sync_checks(ports["a"], ports["b"], ports["c"])
        pulse_checks(ports["a"], ports["b"])
    except Exception:
        traceback.print_exc()
        check("unhandled exception", False, "see traceback")
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print()
    for ok, name, detail in RESULTS:
        line = f"{'PASS' if ok else 'FAIL'}  {name}"
        if detail:
            line += f"   ({detail})"
        print(line)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
