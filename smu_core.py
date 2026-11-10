"""Keysight B2901B (B2900B series) precision SMU control over LAN.

Pure-Python SCPI/VISA layer with no GUI dependencies, so it can be imported
from scripts, notebooks or the Tkinter front-end in keysight_gui.py.

Transport: TCP/IP socket (port 5025), VXI-11, or any full VISA resource
string.  pyvisa-py is enough -- no NI-VISA / Keysight IO Libraries required.
"""

from __future__ import annotations

import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Optional, Sequence

import pyvisa

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

DEFAULT_PORT = 5025          # raw SCPI socket on B2900B series
TIMEOUT_MS = 10_000

VOLT_RANGES = (0.2, 2.0, 20.0, 200.0)                       # V
CURR_RANGES = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 3.0)  # A
MAX_VOLT = 210.0             # B2901B: 210 V
MAX_CURR = 3.03              # B2901B: 3.03 A

WIRE_MODES = ("2W", "4W")    # local (2-wire) or remote/Kelvin (4-wire) sensing


class SmuError(RuntimeError):
    """Anything that went wrong talking to the instrument."""


_NETWORK_HINTS = ("broken pipe", "refused", "timed out", "timeout",
                  "errno 61", "errno 111", "errno 32", "no route",
                  "unreachable", "connection reset")


def _connection_hint(exc: BaseException) -> str:
    text = str(exc).lower()
    if any(key in text for key in _NETWORK_HINTS):
        return ("\n\nThe TCP connection failed: nothing usable is answering at "
                "that address/port. Check the IP, make sure LAN is enabled on "
                "the instrument (I/O > LAN), and that port 5025 is reachable "
                "(try `nc -vz <ip> 5025`).")
    return ""


def _wrap(action: str, exc: BaseException) -> "SmuError":
    if isinstance(exc, SmuError):
        return exc
    return SmuError(f"{action}  ->  {exc}{_connection_hint(exc)}")


# --------------------------------------------------------------------------
# address helpers
# --------------------------------------------------------------------------

def build_address(ip_or_resource: str, port: int = DEFAULT_PORT,
                  transport: str = "socket") -> str:
    """Return a VISA resource string.

    Accepts a bare IPv4 address/hostname, or an already-complete VISA
    resource string which is passed through untouched.
    """
    text = ip_or_resource.strip()
    if not text:
        raise SmuError("Empty address")
    if re.match(r"^(TCPIP|USB|GPIB|ASRL|PXI)", text, re.IGNORECASE):
        return text
    if transport == "socket":
        return f"TCPIP0::{text}::{int(port)}::SOCKET"
    if transport == "vxi11":
        return f"TCPIP0::{text}::inst0::INSTR"
    raise SmuError(f"Unknown transport {transport!r}")


def pick_range(value: float, ranges: Sequence[float]) -> float:
    """Smallest instrument range that covers abs(value)."""
    mag = abs(float(value))
    for r in ranges:
        if mag <= r:
            return r
    return ranges[-1]


def norm_wire(value: object) -> bool:
    """Normalise any 2W/4W spelling into a ``four_wire`` boolean.

    Accepts ``True``/``False``, the ints 2 and 4, and strings such as
    ``"2W"``, ``"4-wire"``, ``"FOURWIRE"``, ``"REMOTE"``, ``"ON"``/``"OFF"``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if int(value) == 4:
            return True
        if int(value) == 2:
            return False
        raise SmuError(f"Unknown wire mode {value!r} (use 2W or 4W)")
    text = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    if text in ("4W", "4WIRE", "FOURWIRE", "FOUR", "4",
                "KELVIN", "REMOTE", "ON", "TRUE"):
        return True
    if text in ("2W", "2WIRE", "TWOWIRE", "TWO", "2", "LOCAL", "OFF", "FALSE"):
        return False
    raise SmuError(f"Unknown wire mode {value!r} (use 2W or 4W)")


def wire_label(four_wire: bool) -> str:
    """Canonical short label for a sensing mode."""
    return "4W" if four_wire else "2W"



def scan_lan(ip_hint: str, port: int = DEFAULT_PORT,
             timeout: float = 0.25,
             workers: int = 48,
             log: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Scan the /24 around *ip_hint* for SCPI instruments on *port*.

    Returns a list of {"ip", "idn"} dicts for hosts that answered *IDN?.
    """
    log = log or (lambda s: None)
    match = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", ip_hint.strip())
    if not match:
        raise SmuError("Enter an IPv4 address first (e.g. 192.168.1.42)")
    base = ".".join(match.groups()[:3])

    def probe(host: str) -> Optional[dict]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            if s.connect_ex((host, port)) != 0:
                return None
            s.settimeout(1.5)
            s.sendall(b"*IDN?\n")
            data = s.recv(1024).decode("ascii", "replace").strip()
            return {"ip": host, "idn": data}
        except OSError:
            return None
        finally:
            s.close()

    hosts = [f"{base}.{i}" for i in range(1, 255)]
    found: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(probe, hosts):
            if result:
                found.append(result)
                log(f"found {result['ip']}: {result['idn']}")
    return found


# --------------------------------------------------------------------------
# instrument
# --------------------------------------------------------------------------

class B2901B:
    """Thin, explicit wrapper around the B2900B SCPI command set."""

    def __init__(self, log: Optional[Callable[[str], None]] = None) -> None:
        self.log = log or (lambda s: None)
        self.rm: Optional[pyvisa.ResourceManager] = None
        self.inst = None
        self.address = ""
        self.idn = ""
        self.timeout_ms = TIMEOUT_MS
        self.backend = ""

    # -- lifecycle ---------------------------------------------------------

    def open(self, address: str, timeout_ms: int = TIMEOUT_MS,
             backend: str = "auto") -> str:
        """Open the resource, query *IDN? and return it."""
        self.close()

        if backend == "auto":
            try:
                self.rm = pyvisa.ResourceManager()          # system VISA if any
            except Exception:
                self.rm = pyvisa.ResourceManager("@py")     # pyvisa-py fallback
            lib = getattr(self.rm.visalib, "library_path", "")
            self.backend = "pyvisa-py" if str(lib) == "py" else str(lib)
        else:
            self.rm = pyvisa.ResourceManager(backend)
            self.backend = backend

        try:
            inst = self.rm.open_resource(address)
        except Exception as exc:
            self._shutdown_rm()
            raise _wrap(f"Cannot open {address}", exc) from exc

        inst.timeout = int(timeout_ms)
        try:
            inst.write_termination = "\n"
            inst.read_termination = "\n"
        except Exception:
            pass

        self.inst = inst
        self.address = address
        self.timeout_ms = int(timeout_ms)

        try:
            idn = self.query("*IDN?").strip()
        except Exception as exc:
            self.close()
            raise SmuError(
                f"Reached {address} but got no answer to *IDN?.\n{exc}"
                + (_connection_hint(exc) or
                   "\n\nThe port opened but the instrument did not reply. If "
                   "the socket is not the SCPI port, try the VXI-11 transport.")
            ) from exc

        self.idn = idn
        if "B290" not in idn.upper():
            self.log(f"warning: unexpected instrument IDN: {idn}")
        self.write("*CLS")
        self.log(f"connected: {address} -> {idn}")
        return idn

    def close(self) -> None:
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
            self.inst = None
        self._shutdown_rm()
        self.idn = ""
        self.address = ""

    def _shutdown_rm(self) -> None:
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
            self.rm = None

    def safe_shutdown(self) -> None:
        """Best effort: output off, front panel back to local, port closed."""
        if self.inst is None:
            return
        for cmd in (":OUTPut OFF", ":SYSTem:LOCal"):
            try:
                self.inst.write(cmd)
            except Exception:
                pass
        self.close()

    @property
    def connected(self) -> bool:
        return self.inst is not None

    # -- raw I/O -----------------------------------------------------------

    def write(self, cmd: str) -> None:
        if self.inst is None:
            raise SmuError("Not connected")
        self.log(f"> {cmd}")
        try:
            self.inst.write(cmd)
        except Exception as exc:                      # noqa: BLE001
            raise _wrap(cmd, exc) from exc

    def query(self, cmd: str) -> str:
        if self.inst is None:
            raise SmuError("Not connected")
        self.log(f"> {cmd}")
        try:
            answer = self.inst.query(cmd).strip()
        except Exception as exc:                      # noqa: BLE001
            raise _wrap(cmd, exc) from exc
        self.log(f"< {answer}")
        return answer

    def read(self) -> str:
        if self.inst is None:
            raise SmuError("Not connected")
        try:
            answer = self.inst.read().strip()
        except Exception as exc:                      # noqa: BLE001
            raise _wrap("read", exc) from exc
        self.log(f"< {answer}")
        return answer

    def errors(self) -> list[str]:
        """Drain and return the SCPI error queue (empty list == clean)."""
        out: list[str] = []
        for _ in range(20):
            try:
                raw = self.query(":SYSTem:ERRor?")
            except SmuError:
                break
            if not raw:
                break
            code = raw.split(",")[0].strip()
            if code in ("0", "+0"):
                break
            out.append(raw)
        return out

    def reset(self) -> None:
        self.write("*RST")
        self.write("*CLS")
        self.check()

    # -- state checks ------------------------------------------------------

    def check(self) -> None:
        """Raise if the instrument reports any SCPI error."""
        errs = self.errors()
        if errs:
            raise SmuError("Instrument error: " + " | ".join(errs))

    def output_state(self) -> bool:
        return self.query(":OUTPut?").strip() in ("1", "ON")

    def output(self, on: bool, verify: bool = True) -> bool:
        """Switch the output and return the state the instrument reports."""
        self.write(f":OUTPut {'ON' if on else 'OFF'}")
        if not verify:
            return on
        state = self.output_state()
        if state != on:
            raise SmuError(
                f"Instrument did not switch output {'ON' if on else 'OFF'} "
                f"(reports {state})."
            )
        return state

    # -- source / measure --------------------------------------------------

    @staticmethod
    def _norm_func(func: str) -> str:
        f = func.strip().upper()
        if f.startswith("V"):
            return "VOLT"
        if f.startswith("I") or f.startswith("C"):
            return "CURR"
        raise SmuError(f"Unknown source function {func!r}")

    def configure(self, sour_func: str = "VOLT", level: float = 0.0,
                  compliance: float = 1e-3, auto_range: bool = True,
                  sour_range: Optional[float] = None,
                  wire: Optional[object] = None) -> None:
        """Set source function, level, compliance limit, ranges and sensing.

        ``wire`` selects the measurement wiring: ``"2W"`` (local sense) or
        ``"4W"`` (remote/Kelvin sense).  Leave it None to keep whatever the
        instrument is already set to.
        """

        src = self._norm_func(sour_func)
        meas = "CURR" if src == "VOLT" else "VOLT"
        level = float(level)
        compliance = float(compliance)

        if src == "VOLT":
            if abs(level) > MAX_VOLT:
                raise SmuError(f"Level {level} V exceeds the 210 V rating")
        else:
            if abs(level) > MAX_CURR:
                raise SmuError(f"Level {level} A exceeds the 3.03 A rating")
        if compliance <= 0:
            raise SmuError("Compliance must be greater than zero")

        self.write("*CLS")          # drop stale errors from earlier commands
        self.write(f":SOURce:FUNCtion:MODE {src}")
        self.write(":SOURce:FUNCtion:SHAPe DC")
        self.write(f':SENSe:FUNCtion "{meas}"')
        if wire is not None:
            self.set_wire(wire)

        # compliance = protection on the quantity we are measuring
        self.write(f":SENSe:{meas}:PROTection {compliance:.9g}")

        if auto_range:
            self.write(f":SOURce:{src}:RANGe:AUTO ON")
            self.write(f":SENSe:{meas}:RANGe:AUTO ON")
        else:
            rng = sour_range or pick_range(
                level, VOLT_RANGES if src == "VOLT" else CURR_RANGES)
            self.write(f":SOURce:{src}:RANGe {rng:.9g}")
            self.write(f":SENSe:{meas}:RANGe:AUTO ON")

        self.write(f":SOURce:{src} {level:.9g}")
        try:
            self.query("*OPC?")
        except SmuError:
            pass
        self.check()

    def set_wire(self, wire: object = "2W") -> bool:
        """Select 2-wire (local) or 4-wire (remote/Kelvin) sensing.

        Sends ``:SENSe:REMote`` and reads the state back, returning True when
        the instrument reports 4-wire sensing enabled.  A refused 4-wire
        request raises rather than leaving the user believing it took effect.
        """
        four = norm_wire(wire)
        self.write(f":SENSe:REMote {'ON' if four else 'OFF'}")
        try:
            raw = self.query(":SENSe:REMote?")
        except SmuError:
            self.check()
            return four
        on = raw.strip().upper() in ("1", "ON")
        if four and not on:
            raise SmuError(
                "Instrument refused 4-wire sensing (remote sense is not "
                "available on the 3 A range)")
        self.check()
        return on

    def wire_state(self) -> str:
        """Read back the sensing mode actually in effect: "2W" or "4W"."""
        return wire_label(self.query(":SENSe:REMote?").strip().upper() in ("1", "ON"))

    def measure(self, func: str = "CURR") -> float:
        """Single spot measurement, returns amps or volts."""
        m = self._norm_func(func)
        raw = self.query(f":MEASure:{m}?")
        return parse_first_float(raw)

    def measure_both(self) -> tuple[float, float]:
        """Return (volts, amps) via :READ? with explicit format elements."""
        self.write(":FORMat:ELEMents:SENSe VOLTage,CURRent")
        self.write(":FORMat ASCii")
        raw = self.query(":READ?")
        return parse_pair(raw)

    # -- pulsed output (instrument timer, hardware-timed) ------------------

    def pulse_config(self, sour_func: str = "VOLT", base: float = 0.0,
                     peak: float = 1.0, width_s: float = 1e-3,
                     period_s: float = 10e-3, pulse_delay_s: float = 0.5e-3,
                     aperture_s: float = 0.1e-3, compliance: float = 1e-3,
                     count: int = 10,
                     acq_delay_s: Optional[float] = None,
                     wire: Optional[object] = None) -> None:
        """Program a hardware-timed pulse train (B2900 :SOUR:PULS).

        base is output continuously; each trigger produces one peak pulse of
        width_s starting pulse_delay_s after the trigger, repeating every
        period_s, count times.  The measurement is centred in the pulse by
        default (acq_delay_s overrides that).
        """
        src = self._norm_func(sour_func)
        meas = "CURR" if src == "VOLT" else "VOLT"
        base, peak, compliance = float(base), float(peak), float(compliance)
        width_s, period_s = float(width_s), float(period_s)
        pulse_delay_s, aperture_s = float(pulse_delay_s), float(aperture_s)
        count = int(count)

        if width_s <= 0 or period_s <= 0:
            raise SmuError("Pulse width and period must be positive")
        if width_s > 10:
            raise SmuError("Pulse width above 10 s is not supported")
        if aperture_s <= 0 or aperture_s > width_s:
            raise SmuError(
                f"Aperture ({aperture_s * 1e6:.0f} us) must be > 0 and "
                f"no wider than the pulse ({width_s * 1e6:.0f} us)")
        if pulse_delay_s + width_s >= period_s:
            raise SmuError(
                f"Pulse delay + width ({pulse_delay_s + width_s:.6g} s) must "
                f"fit inside the period ({period_s:.6g} s)")
        if count < 1:
            raise SmuError("Pulse count must be at least 1")

        src_range = pick_range(max(abs(base), abs(peak)),
                               VOLT_RANGES if src == "VOLT" else CURR_RANGES)
        meas_range = pick_range(compliance,
                                CURR_RANGES if meas == "CURR" else VOLT_RANGES)
        acq = (pulse_delay_s + max(0.0, (width_s - aperture_s) / 2.0)
               if acq_delay_s is None else float(acq_delay_s))

        self.write("*CLS")
        self.write(f":SOURce:FUNCtion:MODE {src}")
        self.write(":SOURce:FUNCtion:SHAPe PULSe")
        self.write(f':SENSe:FUNCtion "{meas}"')
        if wire is not None:
            self.set_wire(wire)
        self.write(f":SOURce:{src}:RANGe:AUTO OFF")
        self.write(f":SOURce:{src}:RANGe {src_range:.9g}")
        self.write(f":SENSe:{meas}:RANGe:AUTO OFF")
        self.write(f":SENSe:{meas}:RANGe {meas_range:.9g}")
        self.write(f":SOURce:{src} {base:.9g}")            # base level
        self.write(f":SOURce:{src}:TRIGger {peak:.9g}")     # pulse peak
        self.write(f":SOURce:PULSe:DELay {pulse_delay_s:.9g}")
        self.write(f":SOURce:PULSe:WIDTh {width_s:.9g}")
        self.write(f":SENSe:{meas}:APERture {aperture_s:.9g}")
        self.write(f":SENSe:{meas}:PROTection {compliance:.9g}")
        self.write(":TRIGger:TRANsient:DELay 0")
        self.write(f":TRIGger:ACQuire:DELay {acq:.9g}")
        self.write(":TRIGger:SOURce TIMer")
        self.write(f":TRIGger:TIMer {period_s:.9g}")
        self.write(f":TRIGger:COUNt {count}")
        self.write(":FORMat:DATA ASCii")
        self.check()

    def pulse_start(self) -> None:
        """Switch the output on at the base level and arm the pulse train."""
        self.write(":OUTPut ON")
        self.write(":INITiate")

    def pulse_fetch(self, func: str = "CURR") -> list[float]:
        """Read the array of results acquired so far."""
        m = self._norm_func(func)
        raw = self.query(f":FETCh:ARRay:{m}?")
        return parse_floats(raw)

    def pulse_cleanup(self, output_off: bool = True) -> None:
        """Stop the train, optionally open the output, restore DC shape."""
        self.abort()
        if output_off:
            try:
                self.write(":OUTPut OFF")
            except SmuError:
                pass
        try:
            self.write(":SOURce:FUNCtion:SHAPe DC")
        except SmuError:
            pass

    def pulse_run(self, count: int, period_s: float, func: str = "CURR",
                  should_stop: Optional[Callable[[], bool]] = None,
                  settle_margin: float = 0.3) -> list[float]:
        """Convenience wrapper for a single unit (program first)."""
        self.pulse_start()
        deadline = time.perf_counter() + count * period_s + settle_margin
        while time.perf_counter() < deadline:
            if should_stop and should_stop():
                self.abort()
                break
            time.sleep(0.05)
        return self.pulse_fetch(func)

    # -- BUS-triggered acquisition (for synchronising two units) -----------

    def prepare_trigger(self) -> None:
        """Put the trigger model in BUS mode, one sample per trigger."""
        self.write(":ABORt")
        self.write(":TRIGger:SOURce BUS")
        self.write(":TRIGger:COUNt 1")
        self.write(":SAMPle:COUNt 1")
        self.write(":FORMat:ELEMents:SENSe VOLTage,CURRent")
        self.write(":FORMat ASCii")

    def arm(self) -> None:
        self.write(":INITiate")

    def trigger(self) -> None:
        self.write("*TRG")

    def abort(self) -> None:
        try:
            self.write(":ABORt")
        except Exception:
            pass

    def restore_auto_trigger(self) -> None:
        """Leave BUS mode again so later :MEAS? calls cannot hang waiting."""
        self.abort()
        try:
            self.write(":TRIGger:SOURce AUTO")
        except Exception:
            pass

    def fetch_both(self) -> tuple[float, float]:
        """Read the last acquisition as (volts, amps) without triggering."""
        return parse_pair(self.query(":FETCh?"))

    def fetch_pair_or_none(self) -> Optional[tuple[float, float]]:
        try:
            return self.fetch_both()
        except SmuError:
            return None

    def sweep(self, sour_func: str, start: float, stop: float, points: int,
              delay_s: float = 0.02,
              on_point: Optional[Callable[[int, float, float], None]] = None,
              should_stop: Optional[Callable[[], bool]] = None) -> list[tuple]:
        """Host-paced linear sweep.  Assumes configure() already ran.

        Returns [(source_value, measured_value), ...].  Stops early (without
        error) when should_stop() returns True.
        """
        src = self._norm_func(sour_func)
        meas = "CURR" if src == "VOLT" else "VOLT"
        n = max(2, int(points))
        step = (stop - start) / (n - 1)
        data: list[tuple] = []

        for i in range(n):
            if should_stop and should_stop():
                break
            x = start + step * i
            self.write(f":SOURce:{src} {x:.9g}")
            if delay_s > 0:
                time.sleep(delay_s)
            y = self.measure(meas)
            data.append((x, y))
            if on_point:
                on_point(i, x, y)
        return data


def parse_first_float(text: str) -> float:
    """Parse '1.234E-03' or '+1.2e-3,2.0' into a float."""
    if text is None:
        raise SmuError("Empty instrument answer")
    parts = [p for p in re.split(r"[,\s]+", str(text).strip()) if p]
    if not parts:
        raise SmuError("Empty instrument answer")
    try:
        return float(parts[0])
    except ValueError as exc:
        raise SmuError(f"Cannot parse instrument answer {text!r}") from exc


def parse_floats(text: str) -> list[float]:
    """Parse ':FETC:ARR?' output (comma separated numbers) into a list."""
    if not text:
        return []
    out: list[float] = []
    for part in re.split(r"[,\s]+", str(text).strip()):
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError as exc:
            raise SmuError(f"Cannot parse data array {str(text)[:120]!r}") from exc
    return out


def parse_pair(text: str) -> tuple[float, float]:
    """Parse ':READ?' / ':FETCh?' output formatted as VOLT,CURR."""
    if text is None:
        raise SmuError("Empty instrument answer")
    parts = [p for p in re.split(r"[,\s]+", str(text).strip()) if p]
    if len(parts) < 2:
        raise SmuError(f"Unexpected measurement answer {text!r}")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise SmuError(f"Cannot parse measurement answer {text!r}") from exc


# --------------------------------------------------------------------------
# two (or more) units driven together
# --------------------------------------------------------------------------

class SyncGroup:
    """Drive several B2901B units as one logical instrument.

    mode="trigger"     BUS-trigger both units, then fetch: the closest thing
                       to simultaneous you can get over LAN (the measured
                       skew between the two *TRG writes is reported).
    mode="parallel"    two threads issue :MEAS? at the same time.
    mode="sequential"  one after the other (slowest, most predictable).

    If BUS triggering is rejected by the firmware, the group falls back to
    parallel automatically and logs why.
    """

    MODES = ("trigger", "parallel", "sequential")

    def __init__(self, units: Sequence["B2901B"],
                 log: Optional[Callable[[str], None]] = None,
                 mode: str = "trigger") -> None:
        self.units = list(units)
        self.log = log or (lambda s: None)
        if not self.units:
            raise SmuError("No instruments in the sync group")
        if mode not in self.MODES:
            raise SmuError(f"Unknown sync mode {mode!r}")
        self.mode = mode
        self.last_skew = 0.0
        self.fell_back = False

    # -- plumbing ----------------------------------------------------------

    def _run(self, fn) -> list:
        """Call fn(unit) on every unit, in parallel when there are 2+."""
        if len(self.units) == 1:
            return [fn(self.units[0])]
        with ThreadPoolExecutor(max_workers=len(self.units)) as pool:
            futures = [pool.submit(fn, unit) for unit in self.units]
            return [f.result() for f in futures]

    def _run_timed(self, fn) -> list[float]:
        """Like _run but returns the perf_counter stamp just before each call."""
        def timed(unit):
            stamp = time.perf_counter()
            fn(unit)
            return stamp
        return self._run(timed)

    def _fallback(self, exc: BaseException) -> None:
        if self.mode != "parallel":
            self.log(f"!! synchronised trigger failed ({exc}); "
                     f"falling back to parallel measurement")
            self.mode = "parallel"
            self.fell_back = True
            for unit in self.units:
                try:
                    unit.restore_auto_trigger()
                except Exception:
                    pass

    # -- operations --------------------------------------------------------

    def configure(self, sour_func: str = "VOLT", level: float = 0.0,
                  compliance: float = 1e-3, auto_range: bool = True,
                  wire: Optional[object] = None) -> None:
        self._run(lambda u: u.configure(sour_func, level, compliance,
                                        auto_range=auto_range, wire=wire))

    def output(self, on: bool) -> list[bool]:
        return self._run(lambda u: u.output(on))

    def output_states(self) -> list[bool]:
        return self._run(lambda u: u.output_state())

    def measure_synced(self, func: str = "CURR") -> tuple[list[float], float]:
        """One measurement per unit.  Returns (values, skew_seconds)."""
        self.last_skew = 0.0
        if self.mode == "trigger":
            try:
                self._run(lambda u: u.prepare_trigger())
                try:
                    self._run(lambda u: u.arm())
                    stamps = self._run_timed(lambda u: u.trigger())
                    self.last_skew = max(stamps) - min(stamps)
                    self._run(lambda u: u.query("*OPC?"))
                    pairs = self._run(lambda u: u.fetch_both())
                finally:
                    self._run(lambda u: u.restore_auto_trigger())
                idx = 1 if B2901B._norm_func(func) == "CURR" else 0
                return [pair[idx] for pair in pairs], self.last_skew
            except Exception as exc:                       # noqa: BLE001
                self._fallback(exc)

        if self.mode == "parallel":
            return self._run(lambda u: u.measure(func)), 0.0

        values = []
        for unit in self.units:
            values.append(unit.measure(func))
        return values, 0.0

    def measure_both_synced(self) -> tuple[list[tuple[float, float]], float]:
        """One (volts, amps) pair per unit.  Returns (pairs, skew_seconds).

        Same trigger machinery as measure_synced(), but keeps both quantities
        so the caller can compute resistance and power without a second
        acquisition.
        """
        self.last_skew = 0.0
        if self.mode == "trigger":
            try:
                self._run(lambda u: u.prepare_trigger())
                try:
                    self._run(lambda u: u.arm())
                    stamps = self._run_timed(lambda u: u.trigger())
                    self.last_skew = max(stamps) - min(stamps)
                    self._run(lambda u: u.query("*OPC?"))
                    pairs = self._run(lambda u: u.fetch_both())
                finally:
                    self._run(lambda u: u.restore_auto_trigger())
                return pairs, self.last_skew
            except Exception as exc:                       # noqa: BLE001
                self._fallback(exc)

        if self.mode == "parallel":
            return self._run(lambda u: u.measure_both()), 0.0

        pairs = []
        for unit in self.units:
            pairs.append(unit.measure_both())
        return pairs, 0.0

    # -- pulsed output -----------------------------------------------------

    def pulse_configure(self, sour_func: str = "VOLT", base: float = 0.0,
                        peak: float = 1.0, width_s: float = 1e-3,
                        period_s: float = 10e-3, pulse_delay_s: float = 0.5e-3,
                        aperture_s: float = 0.1e-3, compliance: float = 1e-3,
                        count: int = 10) -> None:
        self._run(lambda u: u.pulse_config(
            sour_func, base, peak, width_s, period_s, pulse_delay_s,
            aperture_s, compliance, count))

    def pulse_train(self, count: int, period_s: float, func: str = "CURR",
                    should_stop: Optional[Callable[[], bool]] = None,
                    on_tick: Optional[Callable[[float], None]] = None
                    ) -> tuple[list[list[float]], float]:
        """Start the train on every unit and read all arrays when done.

        Returns (per-unit result arrays, skew between the two :INIT commands).
        The instruments keep their own crystal-clocked timing after the start,
        so the only synchronisation error is that start skew (sub-ms).
        """
        self._run(lambda u: u.output(True))
        stamps = self._run_timed(lambda u: u.pulse_start())
        skew = max(stamps) - min(stamps)
        self.last_skew = skew

        deadline = time.perf_counter() + count * period_s + 0.5
        aborted = False
        while time.perf_counter() < deadline:
            if should_stop and should_stop():
                aborted = True
                self._run(lambda u: u.abort())
                break
            time.sleep(0.05)
            if on_tick:
                on_tick(max(0.0, deadline - time.perf_counter()))
        if aborted:
            # give the instrument a moment to stop before reading the buffer
            time.sleep(0.15)
        return self._run(lambda u: u.pulse_fetch(func)), skew

    def pulse_cleanup(self, output_off: bool = True) -> None:
        self._run(lambda u: u.pulse_cleanup(output_off))

    def sweep_synced(self, sour_func: str, start: float, stop: float,
                     points: int, delay_s: float = 0.02,
                     on_point: Optional[Callable[[int, float, list[float]],
                                                 None]] = None,
                     should_stop: Optional[Callable[[], bool]] = None
                     ) -> list[tuple[float, list[float]]]:
        """Same source ramp applied to all units, measured in lockstep."""
        src = B2901B._norm_func(sour_func)
        meas = "CURR" if src == "VOLT" else "VOLT"
        n = max(2, int(points))
        step = (stop - start) / (n - 1)
        data: list[tuple[float, list[float]]] = []

        if self.mode == "trigger":
            try:
                self._run(lambda u: u.prepare_trigger())
            except Exception as exc:                       # noqa: BLE001
                self._fallback(exc)

        try:
            for i in range(n):
                if should_stop and should_stop():
                    break
                x = start + step * i
                self._run(lambda u: u.write(f":SOURce:{src} {x:.9g}"))
                if delay_s > 0:
                    time.sleep(delay_s)

                values: list[float]
                if self.mode == "trigger":
                    try:
                        self._run(lambda u: u.arm())
                        stamps = self._run_timed(lambda u: u.trigger())
                        self.last_skew = max(stamps) - min(stamps)
                        self._run(lambda u: u.query("*OPC?"))
                        pairs = self._run(lambda u: u.fetch_both())
                        idx = 1 if meas == "CURR" else 0
                        values = [pair[idx] for pair in pairs]
                    except Exception as exc:               # noqa: BLE001
                        self._fallback(exc)
                        values = self._run(lambda u: u.measure(meas))
                else:
                    values = self._run(lambda u: u.measure(meas))

                data.append((x, values))
                if on_point:
                    on_point(i, x, values)
        finally:
            if self.mode == "trigger":
                self._run(lambda u: u.restore_auto_trigger())

        return data

    def safe_shutdown(self) -> None:
        self._run(lambda u: u.safe_shutdown())
