# keysight connect

Pure-Python control of one or two **Keysight B2901B** precision SMUs over LAN,
with a Tkinter GUI, a hardware-free mock instrument, and a self-test suite.

No NI-VISA or Keysight IO Libraries needed — `pyvisa-py` plus a TCP socket is
enough (VXI-11 and full VISA resource strings also work).

## Files

| File | Purpose |
| --- | --- |
| `smu_core.py` | GUI-free SCPI/VISA layer: connect, source, measure, sweep, BUS-triggered sync of two units |
| `keysight_gui.py` | Tkinter front-end: Source/Measure, synchronized I-V Sweep, SCPI console |
| `mock_b2901b.py` | Fake B2901B on a TCP port (simulates a 100 Ω load with compliance clipping) |
| `selftest.py` | Exercises `smu_core` against the mocks: single unit, synchronized pair, BUS-trigger fallback |
| `gui_smoke.py` | Drives the real Tk app end-to-end against two mock units |

## Requirements

```
pip install pyvisa pyvisa-py
```

Tkinter ships with python.org builds and Homebrew Python; on Debian/Ubuntu
install `python3-tk`.

## Quick start without hardware

```bash
python3 mock_b2901b.py --port 5025 --verbose
python3 keysight_gui.py --address 127.0.0.1
```

Two units, synchronized:

```bash
python3 mock_b2901b.py --port 5025 --load 100
python3 mock_b2901b.py --port 5026 --load 200
python3 keysight_gui.py --address 127.0.0.1 --address-b 127.0.0.1:5026
```

Tests:

```bash
python3 selftest.py      # exit 0 = pass
python3 gui_smoke.py     # needs the two mocks above running
```

## Real hardware

Give the instrument's LAN address (socket port 5025 is assumed):

```bash
python3 keysight_gui.py --address 192.168.1.42 --address-b 192.168.1.43
```

Every command sent to the instruments is echoed in the SCPI Console tab.

## Notes

* All VISA traffic runs on a single worker thread, so the UI never blocks.
* Two units are driven in lockstep with a BUS trigger; if an instrument
  refuses BUS triggering the code falls back to sequential triggering.
