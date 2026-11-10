#!/usr/bin/env python3
"""mock_b2901b.py -- fake B2901B SCPI instrument on a TCP socket.

Lets you exercise keysight_gui.py (and any script using smu_core) without
hardware.  It speaks a useful subset of the B2900B SCPI command set and
simulates a 100 ohm load with compliance clipping.

    python3 mock_b2901b.py --port 5025 --verbose
    python3 keysight_gui.py --address 127.0.0.1
"""

from __future__ import annotations

import argparse
import re
import socketserver
import sys
import time

LOAD_OHMS = 100.0
LEAD_OHMS = 0.0          # cable + contact resistance seen by 2-wire sensing
                         # (set --lead 0.5 to make 2-wire read high)
IDN = "Keysight Technologies,B2901B,B2901B-MY00000000,3.2.0.0"


class FakeB2901B:
    def __init__(self, load_ohms: float = LOAD_OHMS,
                 support_trigger: bool = True,
                 lead_ohms: float = LEAD_OHMS) -> None:
        self.load_ohms = load_ohms
        self.lead_ohms = lead_ohms
        self.support_trigger = support_trigger
        self.reset()

    def reset(self) -> None:
        self.src_func = "VOLT"
        self.level = 0.0
        self.compliance = 1e-3
        self.output = False
        self.autorange = True
        self.elem = ["VOLT", "CURR"]
        self.errors: list[str] = []
        self.trig_source = "AUTO"
        self.armed = False
        self.buffer: tuple[float, float] | None = None
        # pulse mode
        self.shape = "DC"
        self.peak = 0.0
        self.puls_del = 0.5e-3
        self.puls_widt = 1e-3
        self.trig_tim = 10e-3
        self.trig_coun = 1
        self.acq_del = 0.0
        self.aper = 1e-4
        self.four_wire = False
        self.pulse_t0: float | None = None
        self.pulse_frozen: list[tuple[float, float]] | None = None

    # -- physics -----------------------------------------------------------
    def values_at(self, level: float) -> tuple[float, float]:
        if not self.output:
            return 0.0, 0.0
        # 2-wire sensing measures at the terminals, so the leads' resistance
        # adds to the load; 4-wire senses at the DUT and cancels it.
        r_total = self.load_ohms + (0.0 if self.four_wire else self.lead_ohms)
        if self.src_func == "VOLT":
            i = level / r_total
            if abs(i) > self.compliance:
                i = self.compliance if i > 0 else -self.compliance
            v = i * self.load_ohms          # voltage the sense leads report
            return v, i
        i = level
        v = i * r_total                    # terminal volts in 2-wire
        if abs(v) > self.compliance:
            v = self.compliance if v > 0 else -self.compliance
        return v, i

    def values(self) -> tuple[float, float]:
        return self.values_at(self.level)

    def pulse_pairs(self) -> list[tuple[float, float]]:
        """Samples the instrument would have acquired so far in pulse mode."""
        if self.pulse_frozen is not None:
            return self.pulse_frozen
        if self.pulse_t0 is None or self.trig_tim <= 0:
            return []
        elapsed = time.monotonic() - self.pulse_t0
        n = min(self.trig_coun, int(elapsed / self.trig_tim) + 1)
        return [self.values_at(self.peak)] * max(0, n)

    def formatted(self, values: tuple[float, float]) -> str:
        v, i = values
        out = []
        for elem in self.elem:
            if elem.startswith("VOLT"):
                out.append(f"{v:+.9E}")
            elif elem.startswith("CURR"):
                out.append(f"{i:+.9E}")
            elif elem.startswith("TIME"):
                out.append("+0.000000E+00")
            elif elem.startswith("STAT"):
                out.append("+0")
            elif elem.startswith("SOUR"):
                out.append(f"{self.level:+.9E}")
        return ",".join(out) if out else f"{v:+.9E},{i:+.9E}"

    # -- command handling --------------------------------------------------
    def handle(self, line: str) -> str | None:
        cmd = line.strip()
        if not cmd:
            return None
        upper = cmd.upper()

        if upper == "*IDN?":
            return IDN
        if upper == "*RST":
            self.reset()
            return None
        if upper == "*CLS":
            self.errors.clear()
            return None
        if upper == "*OPC?":
            return "1"
        if upper == "*OPC":
            return None

        if upper.startswith(":SYST:ERR") or upper.startswith(":SYSTEM:ERR"):
            if self.errors:
                err = self.errors.pop(0)
                code = err.split(",")[0]
                return f"{code},\"{err.split(',', 1)[1]}\""
            return '0,"No error"'

        if upper in (":OUTP?", ":OUTPUT?", ":OUTP:STAT?", ":OUTPUT:STAT?"):
            return "1" if self.output else "0"
        if re.match(r"^:OUTP(UT)?(:(STAT(E)?)?)?\s+(ON|OFF|1|0)$", upper):
            value = upper.rsplit(None, 1)[1]
            self.output = value in ("ON", "1")
            return None

        m = re.match(r"^:SOUR(CE)?:FUNC(TION)?(:SHAP(E)?)?\s+(PULS(E)?|DC)$", upper)
        if m:
            self.shape = "PULS" if m.group(5).startswith("PULS") else "DC"
            return None

        if re.match(r"^:SOUR(CE)?:FUNC(TION)?:SHAP(E)?\?$", upper):
            return self.shape

        m = re.match(r"^:SOUR(CE)?:FUNC(TION)?(:MODE)?\s+(\"?)(VOLT|CURR|VOLTAGE|CURRENT)\4$",
                     upper)
        if m:
            word = m.group(5)
            self.src_func = "VOLT" if word.startswith("VOLT") else "CURR"
            return None

        m = re.match(r"^:SOUR(CE)?:(VOLT|CURR)(AGE|ENT)?(:LEV(EL)?)?"
                     r"(:IMM(EDIATE)?)?(:AMPL(ITUDE)?)?\s+([-+0-9.E]+)$", upper)
        if m:
            word = m.group(2)
            value = float(m.group(10))
            if word.startswith("VOLT"):
                self.level = value
                self.src_func = "VOLT"
            else:
                self.level = value
                self.src_func = "CURR"
            return None

        # ---- pulse mode setup
        m = re.match(r"^:SOUR(CE)?:(VOLT|CURR)(AGE|ENT)?:TRIG(GER)?\s+([-+0-9.E]+)$",
                     upper)
        if m:
            self.peak = float(m.group(5))
            self.src_func = "VOLT" if m.group(2).startswith("VOLT") else "CURR"
            return None

        m = re.match(r"^:SOUR(CE)?:PULS(E)?:DEL(AY)?\s+([-+0-9.E]+)$", upper)
        if m:
            self.puls_del = float(m.group(4))
            return None

        m = re.match(r"^:SOUR(CE)?:PULS(E)?:WIDT(H)?\s+([-+0-9.E]+)$", upper)
        if m:
            self.puls_widt = float(m.group(4))
            return None

        m = re.match(r"^:SOUR(CE)?:(VOLT|CURR)(AGE|ENT)?:RANG(E)?:AUTO\s+(ON|OFF|1|0)$",
                     upper)
        if m:
            self.autorange = m.group(5) in ("ON", "1")
            return None
        if re.match(r"^:SOUR(CE)?:(VOLT|CURR)(AGE|ENT)?:RANG(E)?\s+[-+0-9.E]+$", upper):
            return None

        m = re.match(r"^:SENS(E)?:FUNC(TION)?\s+\"?(VOLT|CURR)(AGE|ENT)?\"?$", upper)
        if m:
            return None

        m = re.match(r"^:SENS(E)?:(VOLT|CURR)(AGE|ENT)?:PROT(ECTION)?\s+([-+0-9.E]+)$",
                     upper)
        if m:
            self.compliance = abs(float(m.group(5)))
            return None
        if re.match(r"^:SENS(E)?:(VOLT|CURR)(AGE|ENT)?:RANG(E)?:AUTO\s+(ON|OFF|1|0)$",
                    upper):
            return None

        if re.match(r"^:SENS(E)?:(VOLT|CURR)(AGE|ENT)?:RANG(E)?\s+[-+0-9.E]+$", upper):
            return None

        m = re.match(r"^:SENS(E)?:(VOLT|CURR)(AGE|ENT)?:APER(TURE)?\s+([-+0-9.E]+)$",
                     upper)
        if m:
            self.aper = float(m.group(5))
            return None

        # ---- 4-wire (remote sense)
        m = re.match(r"^:SENS(E)?:REM(OTE)?\s+(ON|OFF|1|0)$", upper)
        if m:
            self.four_wire = m.group(3) in ("ON", "1")
            return None
        if re.match(r"^:SENS(E)?:REM(OTE)?\?$", upper):
            return "1" if self.four_wire else "0"

        m = re.match(r"^:FORM(AT)?:ELEM(ENTS)?:SENS(E)?\s+(.+)$", upper)
        if m:
            self.elem = [e for e in re.split(r"[,\s]+", m.group(4)) if e]
            return None
        if re.match(r"^:FORM(AT)?(:DATA)?\s+(ASC|ASCII|REAL)$", upper):
            return None

        if re.match(r"^:MEAS(URE)?:(VOLT|CURR)(AGE|ENT)?\?$", upper):
            v, i = self.values()
            return f"{v if 'VOLT' in upper else i:+.9E}"

        # ---- trigger model (used by SyncGroup)
        if upper == "*TRG":
            if not self.support_trigger:
                self.errors.append("-113,Undefined header")
            elif self.armed:
                self.buffer = self.values()
                self.armed = False
            else:
                self.errors.append("-211,Trigger ignored")
            return None

        if re.match(r"^:INIT(IATE)?(:IMM(EDIATE)?)?(\s+\(@[0-9,:]+\))?$", upper):
            if self.trig_source in ("TIM", "TIMER"):
                self.pulse_t0 = time.monotonic()
                self.pulse_frozen = None
                self.buffer = None
            elif self.trig_source == "BUS":
                if self.support_trigger:
                    self.armed = True
                else:
                    self.errors.append("-113,Undefined header")
            else:
                self.buffer = self.values()
            return None

        if re.match(r"^:ABOR(T)?$", upper):
            self.pulse_frozen = self.pulse_pairs()
            self.pulse_t0 = None
            self.armed = False
            self.buffer = None
            return None

        m = re.match(r"^:TRIG(GER)?:TRAN(SIENT)?:DEL(AY)?\s+[-+0-9.E]+$", upper)
        if m:
            return None

        m = re.match(r"^:TRIG(GER)?:ACQ(UIRE)?:DEL(AY)?\s+([-+0-9.E]+)$", upper)
        if m:
            self.acq_del = float(m.group(4))
            return None

        m = re.match(r"^:TRIG(GER)?:TIM(ER)?\s+([-+0-9.E]+)$", upper)
        if m:
            self.trig_tim = float(m.group(3))
            return None

        m = re.match(r"^:TRIG(GER)?:COUN(T)?\s+(\d+)$", upper)
        if m:
            self.trig_coun = int(m.group(3))
            return None

        m = re.match(r"^:TRIG(GER)?:SOUR(CE)?\s+(AUTO|BUS|TIM(ER)?|EXT(ERNAL)?"
                     r"|MAN(UAL)?|INT(ERNAL)?)$", upper)
        if m:
            self.trig_source = m.group(3)
            return None

        if re.match(r"^:TRIG(GER)?:SOUR(CE)?\?$", upper):
            return self.trig_source

        if re.match(r"^:SAMP(LE)?:(COUN(T)?|TIM(ER)?)\s+.+$", upper):
            return None

        m = re.match(r"^:FETC(H)?:ARR(AY)?(?::(VOLT|CURR)(AGE|ENT)?)?\?"
                     r"(\s+\(@[0-9,:]+\))?$", upper)
        if m:
            pairs = self.pulse_pairs()
            if not pairs:
                return ""
            idx = 1 if (m.group(3) or "").startswith("CURR") else 0
            return ",".join(f"{p[idx]:+.9E}" for p in pairs)

        if upper in (":READ?", ":FETC?", ":FETCH?"):
            if upper == ":READ?":
                self.buffer = self.values()
            if self.buffer is None or not self.support_trigger:
                return ""
            return self.formatted(self.buffer)

        if upper.startswith(":TRIG"):
            return None
        if upper.startswith(":SYST:LOC") or upper.startswith(":SYST:REM"):
            return None

        self.errors.append("-113,Undefined header")
        return None


class Handler(socketserver.StreamRequestHandler):
    verbose = False

    def handle(self) -> None:
        fake: FakeB2901B = self.server.fake       # type: ignore[attr-defined]
        for raw in self.rfile:
            line = raw.decode("ascii", "replace")
            reply = fake.handle(line)
            if self.verbose:
                print(f"  <- {line.strip()!r}  ->  {reply!r}", flush=True)
            if reply is not None:
                self.wfile.write((reply + "\n").encode("ascii"))
                self.wfile.flush()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port: int = 5025, verbose: bool = False,
          load_ohms: float = LOAD_OHMS,
          support_trigger: bool = True,
          lead_ohms: float = LEAD_OHMS) -> Server:
    """Start the mock in a background-capable server (call serve_forever)."""
    server = Server(("127.0.0.1", port), Handler)
    server.fake = FakeB2901B(load_ohms, support_trigger, lead_ohms)  # type: ignore[attr-defined]
    Handler.verbose = verbose
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5025)
    ap.add_argument("--load", type=float, default=LOAD_OHMS,
                    help="simulated load resistance in ohms")
    ap.add_argument("--lead", type=float, default=LEAD_OHMS,
                    help="lead resistance added by 2-wire sensing, in ohms")
    ap.add_argument("--no-trigger", action="store_true",
                    help="reject BUS triggering (tests the fallback path)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    srv = serve(a.port, a.verbose, a.load, not a.no_trigger, a.lead)
    print(f"mock B2901B listening on 127.0.0.1:{a.port}  "
          f"(load {a.load:g} ohm, lead {a.lead:g} ohm, trigger "
          f"{'off' if a.no_trigger else 'on'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
