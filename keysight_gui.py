#!/usr/bin/env python3
"""keysight_gui.py -- Tkinter GUI for one or two Keysight B2901B SMUs over LAN.

Two units can be connected at once and driven synchronised: same source
settings, output switched together, measurements and sweeps taken in lockstep
with a BUS trigger on both instruments.

Tabs
  Source / Measure : source function, level, compliance; output on/off;
                     single or continuous measurement with live plot + CSV.
  I-V Sweep        : synchronised linear sweep of both units, plot + CSV.
  SCPI Console     : every command sent, plus a manual command line.

All VISA traffic runs on one worker thread, so the UI never blocks.

    python3 keysight_gui.py
    python3 keysight_gui.py --address 192.168.1.42 --address-b 192.168.1.43
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import smu_core as smu

CONFIG = Path.home() / ".keysight_b2901b_gui.json"

BG = "#f4f5f7"
PANEL = "#ffffff"
INK = "#1d1f23"
MUTED = "#6b7280"
ACCENT = "#0b63ce"
ACCENT_B = "#d97706"
OFF_BG = "#3f4550"
ON_BG = "#c0291e"
OK_GREEN = "#15803d"
IDLE = "#9aa0a6"

COLORS = {"A": ACCENT, "B": ACCENT_B}


def short_idn(idn: str) -> str:
    """'Keysight Technologies,B2901B,MY12345678,3.2.0' -> 'B2901B · MY12345678'."""
    parts = [p.strip() for p in idn.split(",") if p.strip()]
    if len(parts) >= 3:
        return f"{parts[1]}  ·  {parts[2]}"
    return idn


# --------------------------------------------------------------------------
# worker thread
# --------------------------------------------------------------------------

class Worker:
    """Serialises VISA calls onto one daemon thread."""

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="visa", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                return
            fn, fut = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except BaseException as exc:          # noqa: BLE001 - re-raised in UI
                fut.set_exception(exc)

    def submit(self, fn) -> Future:
        fut: Future = Future()
        self.q.put((fn, fut))
        return fut

    def shutdown(self) -> None:
        self.q.put(None)


# --------------------------------------------------------------------------
# one instrument slot (A or B)
# --------------------------------------------------------------------------

class DeviceSlot:

    def __init__(self, app: "App", letter: str) -> None:
        self.app = app
        self.letter = letter
        self.inst = smu.B2901B(log=lambda s: app.logq.put(f"[{letter}] {s}"))
        self.connected = False
        self.output_on = False
        self.src_applied = "VOLT"
        self.enabled = letter == "A"

        # measurement history
        self.t_data: list[float] = []
        self.y_data: list[float] = []
        self.n = 0
        self.y_min = float("inf")
        self.y_max = float("-inf")
        self.y_sum = 0.0

        self._widgets: list[tk.Widget] = []

    # -- widgets -----------------------------------------------------------

    def build_row(self, card: ttk.LabelFrame, row: int) -> None:
        pad = (0, 8)

        if self.letter == "A":
            head = ttk.Label(card, text="Device A", style="Head.TLabel")
            head.grid(row=row * 2, column=0, rowspan=2, sticky="w", padx=pad)
        else:
            self.enabled_var = tk.BooleanVar(value=False)
            chk = ttk.Checkbutton(card, text="Device B",
                                  variable=self.enabled_var,
                                  command=self.app._on_b_toggle)
            chk.grid(row=row * 2, column=0, rowspan=2, sticky="w", padx=pad)
            self._widgets.append(chk)

        ttk.Label(card, text="Transport", style="Muted.TLabel").grid(
            row=row * 2, column=1, sticky="w")
        self.transport_var = tk.StringVar(value="Socket (5025)")
        w = ttk.Combobox(card, textvariable=self.transport_var, width=14,
                         state="readonly",
                         values=("Socket (5025)", "VXI-11", "VISA string"))
        w.grid(row=row * 2 + 1, column=1, padx=pad, sticky="w")

        ttk.Label(card, text="Address", style="Muted.TLabel").grid(
            row=row * 2, column=2, sticky="w")
        self.ip_var = tk.StringVar(value="")
        w = ttk.Entry(card, textvariable=self.ip_var, width=17)
        w.grid(row=row * 2 + 1, column=2, padx=pad, sticky="w")
        w.bind("<Return>", lambda _e: self.app._connect_slot(self))

        ttk.Label(card, text="Port", style="Muted.TLabel").grid(
            row=row * 2, column=3, sticky="w")
        self.port_var = tk.StringVar(value=str(smu.DEFAULT_PORT))
        w = ttk.Entry(card, textvariable=self.port_var, width=6)
        w.grid(row=row * 2 + 1, column=3, padx=pad, sticky="w")

        ttk.Label(card, text="Timeout ms", style="Muted.TLabel").grid(
            row=row * 2, column=4, sticky="w")
        self.timeout_var = tk.StringVar(value="10000")
        w = ttk.Entry(card, textvariable=self.timeout_var, width=7)
        w.grid(row=row * 2 + 1, column=4, padx=pad, sticky="w")

        self.btn_connect = ttk.Button(card, text="Connect",
                                      command=lambda: self.app._connect_slot(self))
        self.btn_connect.grid(row=row * 2 + 1, column=5, padx=pad)

        self.btn_scan = ttk.Button(card, text="Scan",
                                   command=lambda: self.app._scan(self))
        self.btn_scan.grid(row=row * 2 + 1, column=6, padx=pad)

        self.idn_var = tk.StringVar(value="not connected")
        lbl = tk.Label(card, textvariable=self.idn_var, bg=BG, fg=MUTED,
                       font=(self.app.mono, 10), anchor="w")
        lbl.grid(row=row * 2 + 1, column=7, sticky="ew", padx=(6, 0))

        self._widgets += [w for w in (self.btn_connect, self.btn_scan)]
        for child in card.grid_slaves():
            info = child.grid_info()
            if int(info["row"]) in (row * 2, row * 2 + 1) and child not in self._widgets:
                self._widgets.append(child)      # type: ignore[arg-type]

        if self.letter == "B":
            self.set_enabled(False)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        state = "normal" if enabled else "disabled"
        for w in self._widgets:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass

    def set_connected(self, connected: bool, idn: str = "") -> None:
        self.connected = connected
        if connected:
            self.idn_full = idn
            self.idn_var.set(short_idn(idn))
            self.btn_connect.configure(text="Disconnect", state="normal")
        else:
            self.idn_full = ""
            self.idn_var.set("not connected")
            self.btn_connect.configure(text="Connect",
                                       state="normal" if self.enabled else "disabled")
            self.output_on = False

    def reset_history(self) -> None:
        self.t_data.clear()
        self.y_data.clear()
        self.n = 0
        self.y_min = float("inf")
        self.y_max = float("-inf")
        self.y_sum = 0.0


# --------------------------------------------------------------------------
# main window
# --------------------------------------------------------------------------

class App(tk.Tk):

    def __init__(self, address_a: str | None = None,
                 address_b: str | None = None) -> None:
        super().__init__()
        self.title("Keysight B2901B — LAN control")
        self.geometry("1200x830")
        self.minsize(1080, 760)
        self.configure(bg=BG)

        self.worker = Worker()
        self.logq: queue.Queue[str] = queue.Queue()
        self.pointq: queue.Queue[tuple] = queue.Queue()
        self.mono = "Menlo" if "Menlo" in tkfont.families() else "Courier"

        self.polling = False
        self.busy = False
        self.pulse_running = False
        self.pulse_abort = False
        self.pulse_letters: list[str] = ["A"]
        self.pulse_data: dict[str, list[float]] = {}
        self.pulse_deadline = 0.0
        self.sweep_running = False
        self.sweep_abort = False
        self.sweep_total = 0
        self.sweep_data: list[tuple[float, list[float]]] = []
        self.sync_mode = "trigger"
        self.t0 = 0.0
        self.csv_file = None
        self.csv_writer = None

        self._build_style()
        self._build_ui()

        self.slots: dict[str, DeviceSlot] = {
            "A": DeviceSlot(self, "A"),
            "B": DeviceSlot(self, "B"),
        }
        # slots are created after the UI so that widgets exist for their rows;
        # rebuild the connection rows now that the slot objects exist
        self._build_device_rows()
        self._on_b_toggle(silent=True)

        self._load_config()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain_queues)

        if address_a:
            self.slots["A"].ip_var.set(address_a)
            self.after(400, lambda: self._connect_slot(self.slots["A"]))
        if address_b:
            self.slots["B"].ip_var.set(address_b)
            self.slots["B"].enabled_var.set(True)
            self._on_b_toggle()
            self.after(700, lambda: self._connect_slot(self.slots["B"]))

    # -- styling -----------------------------------------------------------

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.configure("TFrame", background=BG)
        style.configure("Card.TLabelframe", background=BG, borderwidth=1,
                        relief="solid")
        style.configure("Card.TLabelframe.Label", background=BG,
                        font=("Helvetica", 12, "bold"), foreground=INK)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=("Helvetica", 10))
        style.configure("Head.TLabel", background=BG, foreground=INK,
                        font=("Helvetica", 11, "bold"))
        style.configure("TButton", padding=(9, 4))
        style.configure("TCheckbutton", background=BG)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 7))
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", background=BG, foreground=INK)

    # -- layout ------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=(12, 8, 12, 8))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        self.conn_card = ttk.LabelFrame(outer, text="Instruments",
                                        style="Card.TLabelframe",
                                        padding=(10, 6, 10, 8))
        self.conn_card.grid(row=0, column=0, sticky="ew")
        self.conn_card.columnconfigure(7, weight=1)

        self._build_sync_row(outer)
        self._build_tabs(outer)
        self._build_statusbar(outer)

    def _build_device_rows(self) -> None:
        self.slots["A"].build_row(self.conn_card, 0)
        self.slots["B"].build_row(self.conn_card, 1)

    def _build_sync_row(self, parent) -> None:
        row = ttk.Frame(parent)
        row.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        row.columnconfigure(6, weight=1)

        ttk.Label(row, text="Synchronisation", style="Head.TLabel").grid(
            row=0, column=0, padx=(2, 8))
        self.sync_mode_var = tk.StringVar(value="Triggered (BUS)")
        combo = ttk.Combobox(row, textvariable=self.sync_mode_var, width=22,
                             state="readonly",
                             values=("Triggered (BUS)", "Parallel",
                                     "Sequential (A then B)"))
        combo.grid(row=0, column=1, padx=(0, 12))
        combo.bind("<<ComboboxSelected>>", self._on_sync_mode)

        self.skew_var = tk.StringVar(value="skew —")
        ttk.Label(row, textvariable=self.skew_var, style="Muted.TLabel").grid(
            row=0, column=2, padx=(0, 16))

        ttk.Button(row, text="Connect both", command=self._connect_both).grid(
            row=0, column=3, padx=(0, 6))
        ttk.Button(row, text="Disconnect all", command=self._disconnect_all).grid(
            row=0, column=4)

        ttk.Label(row, text="BUS trigger = closest to simultaneous over LAN "
                            "(sub-ms command skew)", style="Muted.TLabel").grid(
            row=0, column=6, sticky="e")

    def _build_tabs(self, parent) -> None:
        self.nb = ttk.Notebook(parent)
        self.nb.grid(row=2, column=0, sticky="nsew")
        self.tab_sm = ttk.Frame(self.nb, padding=10)
        self.tab_sweep = ttk.Frame(self.nb, padding=10)
        self.tab_pulse = ttk.Frame(self.nb, padding=10)
        self.tab_scpi = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.tab_sm, text="Source / Measure")
        self.nb.add(self.tab_sweep, text="I-V Sweep")
        self.nb.add(self.tab_pulse, text="Pulses")
        self.nb.add(self.tab_scpi, text="SCPI Console")
        self._build_tab_source()
        self._build_tab_sweep()
        self._build_tab_pulse()
        self._build_tab_scpi()

    # .... tab 1
    def _build_tab_source(self) -> None:
        t = self.tab_sm
        t.columnconfigure(0, minsize=330, weight=0)
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)

        src = ttk.LabelFrame(t, text="Source", style="Card.TLabelframe",
                             padding=14)
        src.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        src.columnconfigure(1, weight=1)

        ttk.Label(src, text="Function").grid(row=0, column=0, sticky="w")
        self.src_func_var = tk.StringVar(value="Voltage")
        combo = ttk.Combobox(src, textvariable=self.src_func_var,
                             state="readonly", values=("Voltage", "Current"))
        combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 12))
        combo.bind("<<ComboboxSelected>>", self._on_func_change)

        ttk.Label(src, text="Level").grid(row=2, column=0, sticky="w")
        ttk.Label(src, text="setpoint", style="Muted.TLabel").grid(
            row=2, column=1, sticky="e")
        lvl = ttk.Frame(src)
        lvl.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 12))
        lvl.columnconfigure(0, weight=1)
        self.level_var = tk.StringVar(value="1.0")
        ttk.Entry(lvl, textvariable=self.level_var,
                  font=(self.mono, 13)).grid(row=0, column=0, sticky="ew")
        self.level_unit = ttk.Label(lvl, text="V", width=3,
                                    font=("Helvetica", 13, "bold"))
        self.level_unit.grid(row=0, column=1, padx=(8, 0))

        ttk.Label(src, text="Compliance").grid(row=4, column=0, sticky="w")
        ttk.Label(src, text="limit", style="Muted.TLabel").grid(
            row=4, column=1, sticky="e")
        comp = ttk.Frame(src)
        comp.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 12))
        comp.columnconfigure(0, weight=1)
        self.compl_var = tk.StringVar(value="0.01")
        ttk.Entry(comp, textvariable=self.compl_var,
                  font=(self.mono, 13)).grid(row=0, column=0, sticky="ew")
        self.compl_unit = ttk.Label(comp, text="A", width=3,
                                    font=("Helvetica", 13, "bold"))
        self.compl_unit.grid(row=0, column=1, padx=(8, 0))

        self.autorange_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(src, text="Auto range", variable=self.autorange_var).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Button(src, text="Apply settings", command=self._apply_source).grid(
            row=7, column=0, columnspan=2, sticky="ew")

        ttk.Separator(src).grid(row=8, column=0, columnspan=2, sticky="ew",
                                pady=14)

        self.out_btn = tk.Label(src, text="OUTPUT  OFF", bg=OFF_BG, fg="white",
                                font=("Helvetica", 15, "bold"), height=2,
                                cursor="hand2")
        self.out_btn.grid(row=9, column=0, columnspan=2, sticky="ew")
        self.out_btn.bind("<Button-1>", lambda _e: self._toggle_output())

        ttk.Label(src, text="Applies the same settings to every connected, "
                            "enabled unit.", style="Muted.TLabel",
                  wraplength=290).grid(row=10, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))

        # ---- measure panel
        meas = ttk.LabelFrame(t, text="Measurement", style="Card.TLabelframe",
                              padding=10)
        meas.grid(row=0, column=1, sticky="nsew")
        meas.columnconfigure(0, weight=1)
        meas.rowconfigure(3, weight=1)

        controls = ttk.Frame(meas)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Button(controls, text="Measure once",
                   command=self._measure_once).pack(side="left")
        self.btn_run = ttk.Button(controls, text="Run continuous",
                                  command=self._toggle_continuous)
        self.btn_run.pack(side="left", padx=8)
        ttk.Label(controls, text="interval", style="Muted.TLabel").pack(
            side="left", padx=(12, 4))
        self.interval_var = tk.StringVar(value="250")
        ttk.Entry(controls, textvariable=self.interval_var, width=6).pack(
            side="left")
        ttk.Label(controls, text="ms", style="Muted.TLabel").pack(
            side="left", padx=(4, 0))

        readings = ttk.Frame(meas)
        readings.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        self.read_vars: dict[str, tk.StringVar] = {}
        self.read_units: dict[str, tk.Label] = {}
        self.read_blocks: dict[str, ttk.Frame] = {}
        for col, letter in enumerate(("A", "B")):
            readings.columnconfigure(col, weight=1)
            frame = ttk.Frame(readings)
            frame.grid(row=0, column=col, sticky="w", padx=(0, 28))
            self.read_blocks[letter] = frame
            tag = ttk.Label(frame, text=f"Unit {letter}", style="Muted.TLabel")
            tag.pack(anchor="w")
            line = ttk.Frame(frame)
            line.pack(anchor="w")
            var = tk.StringVar(value="—")
            self.read_vars[letter] = var
            tk.Label(line, textvariable=var, bg=BG, fg=COLORS[letter],
                     font=(self.mono, 30, "bold")).pack(side="left")
            unit = tk.Label(line, text="A", bg=BG, fg=COLORS[letter],
                            font=("Helvetica", 16, "bold"))
            unit.pack(side="left", anchor="s", padx=(6, 0), pady=(0, 5))
            self.read_units[letter] = unit

        # stats table
        stats = ttk.Frame(meas)
        stats.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for col, text in enumerate(("", "N", "min", "max", "mean")):
            stats.columnconfigure(col, minsize=78 if col else 20)
            ttk.Label(stats, text=text, style="Muted.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0, 18), pady=(0, 2))
        self.stat_vars: dict[str, dict[str, tk.StringVar]] = {}
        for row, letter in enumerate(("A", "B"), start=1):
            tk.Label(stats, text=letter, bg=BG, fg=COLORS[letter],
                     font=("Helvetica", 10, "bold")).grid(row=row, column=0,
                                                         sticky="w")
            self.stat_vars[letter] = {}
            for col, key in enumerate(("n", "min", "max", "mean"), start=1):
                var = tk.StringVar(value="—")
                self.stat_vars[letter][key] = var
                tk.Label(stats, textvariable=var, bg=BG, fg=INK,
                         font=(self.mono, 10)).grid(row=row, column=col,
                                                    sticky="w", padx=(0, 26))

        holder = ttk.Frame(meas)
        holder.grid(row=3, column=0, sticky="nsew")
        self.fig_sm = Figure(figsize=(5, 3), dpi=100, facecolor="white")
        self.ax_sm = self.fig_sm.add_subplot(111)
        self._style_axes(self.ax_sm, "time (s)", "current (A)")
        self.canvas_sm = FigureCanvasTkAgg(self.fig_sm, master=holder)
        self.canvas_sm.get_tk_widget().pack(fill="both", expand=True)
        self._attach_canvas(self.fig_sm, self.canvas_sm)

        logrow = ttk.Frame(meas)
        logrow.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.csv_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(logrow, text="Log to CSV", variable=self.csv_var).pack(
            side="left")
        self.csv_path_var = tk.StringVar(value="")
        ttk.Label(logrow, textvariable=self.csv_path_var,
                  style="Muted.TLabel").pack(side="left", padx=8)
        ttk.Button(logrow, text="Choose…", command=self._choose_csv).pack(
            side="left")
        ttk.Button(logrow, text="Clear plot", command=self._clear_plot).pack(
            side="right")

    # .... tab 2
    def _build_tab_sweep(self) -> None:
        t = self.tab_sweep
        t.columnconfigure(0, minsize=330, weight=0)
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)

        box = ttk.LabelFrame(t, text="Sweep setup", style="Card.TLabelframe",
                             padding=14)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        box.columnconfigure(0, weight=1)
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Source").grid(row=0, column=0, sticky="w")
        self.sw_func_var = tk.StringVar(value="Voltage")
        cb = ttk.Combobox(box, textvariable=self.sw_func_var, state="readonly",
                          values=("Voltage", "Current"))
        cb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 12))
        cb.bind("<<ComboboxSelected>>", self._on_sweep_func_change)

        self.sw_start_var = tk.StringVar(value="0")
        self.sw_stop_var = tk.StringVar(value="1")
        self.sw_points_var = tk.StringVar(value="51")
        self.sw_delay_var = tk.StringVar(value="20")
        self.sw_compl_var = tk.StringVar(value="0.01")

        for i, (label, var, unit) in enumerate((
                ("Start", self.sw_start_var, "V"),
                ("Stop", self.sw_stop_var, "V"),
                ("Points", self.sw_points_var, ""),
                ("Settle delay", self.sw_delay_var, "ms"),
                ("Compliance", self.sw_compl_var, "A")), start=2):
            ttk.Label(box, text=label).grid(row=i * 2, column=0, sticky="w")
            ttk.Label(box, text=unit, style="Muted.TLabel").grid(
                row=i * 2, column=1, sticky="e")
            ttk.Entry(box, textvariable=var, font=(self.mono, 12)).grid(
                row=i * 2 + 1, column=0, columnspan=2, sticky="ew",
                pady=(2, 10))

        self.btn_sweep = ttk.Button(box, text="Run sweep",
                                    command=self._run_sweep)
        self.btn_sweep.grid(row=20, column=0, columnspan=2, sticky="ew",
                            pady=(6, 0))
        self.sw_progress = tk.StringVar(value="idle")
        ttk.Label(box, textvariable=self.sw_progress, style="Muted.TLabel",
                  wraplength=290).grid(row=21, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))
        self.btn_export = ttk.Button(box, text="Export CSV…",
                                     command=self._export_sweep,
                                     state="disabled")
        self.btn_export.grid(row=22, column=0, columnspan=2, sticky="ew",
                             pady=(8, 0))
        ttk.Label(box, text="Both outputs switch on for the sweep and off "
                            "afterwards. Each point is BUS-triggered on both "
                            "units at the same bias.", style="Muted.TLabel",
                  wraplength=290).grid(row=23, column=0, columnspan=2,
                                       sticky="w", pady=(10, 0))

        holder = ttk.Frame(t)
        holder.grid(row=0, column=1, sticky="nsew")
        self.fig_sw = Figure(figsize=(5, 3), dpi=100, facecolor="white")
        self.ax_sw = self.fig_sw.add_subplot(111)
        self._style_axes(self.ax_sw, "voltage (V)", "current (A)")
        self.canvas_sw = FigureCanvasTkAgg(self.fig_sw, master=holder)
        self.canvas_sw.get_tk_widget().pack(fill="both", expand=True)
        self._attach_canvas(self.fig_sw, self.canvas_sw)

    # .... tab 3: pulses
    def _build_tab_pulse(self) -> None:
        t = self.tab_pulse
        t.columnconfigure(0, minsize=340, weight=0)
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)

        box = ttk.LabelFrame(t, text="Pulse generator", style="Card.TLabelframe",
                             padding=12)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        box.columnconfigure(0, weight=1)
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Source").grid(row=0, column=0, sticky="w")
        self.pu_func_var = tk.StringVar(value="Voltage")
        cb = ttk.Combobox(box, textvariable=self.pu_func_var, state="readonly",
                          values=("Voltage", "Current"))
        cb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 10))
        cb.bind("<<ComboboxSelected>>", self._on_pulse_func_change)

        self.pu_base_var = tk.StringVar(value="0")
        self.pu_peak_var = tk.StringVar(value="1.0")
        self.pu_width_var = tk.StringVar(value="1000")
        self.pu_period_var = tk.StringVar(value="10")
        self.pu_count_var = tk.StringVar(value="100")
        self.pu_delay_var = tk.StringVar(value="500")
        self.pu_aper_var = tk.StringVar(value="100")
        self.pu_compl_var = tk.StringVar(value="0.01")
        self.pu_units: dict[str, ttk.Label] = {}

        self._pulse_field(box, 2, 0, "Base level", self.pu_base_var, "V", "base")
        self._pulse_field(box, 2, 1, "Pulse level", self.pu_peak_var, "V", "peak")
        self._pulse_field(box, 3, 0, "Pulse width", self.pu_width_var, "µs")
        self._pulse_field(box, 3, 1, "Period", self.pu_period_var, "ms")
        self._pulse_field(box, 4, 0, "Count", self.pu_count_var, "")
        self._pulse_field(box, 4, 1, "Pulse delay", self.pu_delay_var, "µs")
        self._pulse_field(box, 5, 0, "Aperture", self.pu_aper_var, "µs")
        self._pulse_field(box, 5, 1, "Compliance", self.pu_compl_var, "A",
                          "compl")

        self.pu_outoff_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Output off when finished",
                        variable=self.pu_outoff_var).grid(
            row=12, column=0, columnspan=2, sticky="w", pady=(2, 8))

        self.btn_pulse = ttk.Button(box, text="Run pulse train",
                                    command=self._run_pulses)
        self.btn_pulse.grid(row=13, column=0, columnspan=2, sticky="ew")
        self.pu_progress = tk.StringVar(value="idle")
        ttk.Label(box, textvariable=self.pu_progress, style="Muted.TLabel",
                  wraplength=300).grid(row=14, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))
        self.btn_pulse_export = ttk.Button(box, text="Export CSV…",
                                           command=self._export_pulses,
                                           state="disabled")
        self.btn_pulse_export.grid(row=15, column=0, columnspan=2, sticky="ew",
                                   pady=(8, 0))
        ttk.Label(box, text="Timing comes from the instrument's own timer "
                            "trigger (:SOUR:PULS), so width and period are "
                            "hardware-accurate. The measurement aperture is "
                            "centred inside each pulse.",
                  style="Muted.TLabel", wraplength=300).grid(
            row=16, column=0, columnspan=2, sticky="w", pady=(10, 0))

        holder = ttk.Frame(t)
        holder.grid(row=0, column=1, sticky="nsew")
        holder.rowconfigure(1, weight=1)
        holder.columnconfigure(0, weight=1)

        self.pu_summary = tk.StringVar(value="no pulses yet")
        ttk.Label(holder, textvariable=self.pu_summary, style="Muted.TLabel",
                  wraplength=640).grid(row=0, column=0, sticky="w",
                                       pady=(0, 6))

        self.fig_pu = Figure(figsize=(5, 3), dpi=100, facecolor="white")
        self.ax_pu = self.fig_pu.add_subplot(111)
        self._style_axes(self.ax_pu, "pulse number", "current (A)")
        self.canvas_pu = FigureCanvasTkAgg(self.fig_pu, master=holder)
        self.canvas_pu.get_tk_widget().grid(row=1, column=0, sticky="nsew")
        self._attach_canvas(self.fig_pu, self.canvas_pu)

        stats = ttk.Frame(holder)
        stats.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for col, text in enumerate(("", "N", "mean", "min", "max", "last")):
            stats.columnconfigure(col, minsize=78 if col else 20)
            ttk.Label(stats, text=text, style="Muted.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0, 16))
        self.pu_stat_vars: dict[str, dict[str, tk.StringVar]] = {}
        for row, letter in enumerate(("A", "B"), start=1):
            tk.Label(stats, text=letter, bg=BG, fg=COLORS[letter],
                     font=("Helvetica", 10, "bold")).grid(row=row, column=0,
                                                         sticky="w")
            self.pu_stat_vars[letter] = {}
            for col, key in enumerate(("n", "mean", "min", "max", "last"),
                                      start=1):
                var = tk.StringVar(value="—")
                self.pu_stat_vars[letter][key] = var
                tk.Label(stats, textvariable=var, bg=BG, fg=INK,
                         font=(self.mono, 10)).grid(row=row, column=col,
                                                    sticky="w", padx=(0, 16))

    def _pulse_field(self, parent, row: int, col: int, label: str,
                     var: tk.StringVar, unit: str, key: str = "") -> None:
        cell = ttk.Frame(parent)
        cell.grid(row=row, column=col, sticky="ew",
                  padx=(0, 8) if col == 0 else (0, 0), pady=(0, 6))
        cell.columnconfigure(0, weight=1)
        ttk.Label(cell, text=label, style="Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Entry(cell, textvariable=var, font=(self.mono, 11)).grid(
            row=1, column=0, sticky="ew")
        unit_label = ttk.Label(cell, text=unit, style="Muted.TLabel")
        unit_label.grid(row=1, column=1, sticky="w", padx=(5, 0))
        if key:
            self.pu_units[key] = unit_label

    # .... tab 4
    def _build_tab_scpi(self) -> None:
        t = self.tab_scpi
        t.columnconfigure(0, weight=1)
        t.rowconfigure(1, weight=1)

        quick = ttk.Frame(t)
        quick.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(quick, text="Send to", style="Muted.TLabel").pack(
            side="left", padx=(0, 6))
        self.scpi_target_var = tk.StringVar(value="A + B")
        ttk.Combobox(quick, textvariable=self.scpi_target_var, width=7,
                     state="readonly", values=("A", "B", "A + B")).pack(
            side="left", padx=(0, 14))
        for label, cmd in (("*IDN?", "*IDN?"), ("Reset (*RST)", "*RST"),
                           ("Errors", ":SYSTem:ERRor?"),
                           ("Output OFF", ":OUTPut OFF"),
                           ("Output state", ":OUTPut?"), ("Clear", "*CLS")):
            ttk.Button(quick, text=label,
                       command=lambda c=cmd: self._send_scpi(c)).pack(
                side="left", padx=(0, 6))

        frame = ttk.Frame(t)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.console = tk.Text(frame, bg="#14161a", fg="#d7dae0", wrap="none",
                               font=(self.mono, 11), relief="flat",
                               padx=10, pady=8)
        self.console.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.console.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.console.configure(yscrollcommand=sb.set, state="disabled")
        self.console.tag_configure("out", foreground="#8ad1ff")
        self.console.tag_configure("err", foreground="#ff8a7a")
        self.console.tag_configure("sys", foreground="#9aa0a6")

        cmdrow = ttk.Frame(t)
        cmdrow.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        cmdrow.columnconfigure(0, weight=1)
        self.cmd_var = tk.StringVar(value="")
        self.cmd_entry = ttk.Entry(cmdrow, textvariable=self.cmd_var,
                                   font=(self.mono, 12))
        self.cmd_entry.grid(row=0, column=0, sticky="ew")
        self.cmd_entry.bind("<Return>", lambda _e: self._send_scpi())
        self.cmd_entry.bind("<Up>", self._history_up)
        self.cmd_entry.bind("<Down>", self._history_down)
        ttk.Button(cmdrow, text="Send",
                   command=lambda: self._send_scpi()).grid(row=0, column=1,
                                                          padx=(8, 0))
        self.history: list[str] = []
        self.hist_idx = 0

    # .... status bar
    def _build_statusbar(self, parent) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        bar.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(
            value="Disconnected — enter the instrument's IP and press Connect")
        ttk.Label(bar, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w")

        self.panic = tk.Label(bar, text="OUTPUT OFF", bg=OFF_BG, fg="white",
                              font=("Helvetica", 11, "bold"), padx=14, pady=4,
                              cursor="hand2")
        self.panic.grid(row=0, column=1, sticky="e")
        self.panic.bind("<Button-1>", lambda _e: self._panic())

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _style_axes(ax, xlabel: str, ylabel: str) -> None:
        ax.set_facecolor("white")
        ax.grid(True, color="#e3e6ea", linewidth=0.8)
        ax.set_xlabel(xlabel, fontsize=9, color="#4b5563")
        ax.set_ylabel(ylabel, fontsize=9, color="#4b5563")
        ax.tick_params(labelsize=8, colors="#4b5563")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    @staticmethod
    def _attach_canvas(fig, canvas) -> None:
        """Keep the matplotlib figure sized to its Tk container."""
        widget = canvas.get_tk_widget()

        def on_resize(event) -> None:
            if event.width < 80 or event.height < 80:
                return
            width, height = event.width / fig.dpi, event.height / fig.dpi
            cur_w, cur_h = fig.get_size_inches()
            if abs(width - cur_w) > 0.25 or abs(height - cur_h) > 0.25:
                fig.set_size_inches(width, height, forward=True)
                try:
                    fig.tight_layout()
                except Exception:
                    pass
                canvas.draw_idle()

        widget.bind("<Configure>", on_resize)

    def _drain_queues(self) -> None:
        while True:
            try:
                line = self.logq.get_nowait()
            except queue.Empty:
                break
            tag = "out" if "<" in line.split("] ", 1)[-1][:2] else "sys"
            self._console_write(line, tag)

        while True:
            try:
                item = self.pointq.get_nowait()
            except queue.Empty:
                break
            if item[0] == "point":
                _, i, x, values = item
                self.sweep_data.append((x, list(values)))
                self.sw_progress.set(
                    f"point {i + 1}/{self.sweep_total}   {x:.6g} → "
                    + "  ".join(f"{v:.6g}" for v in values))
                self._plot_sweep()

        self.after(60, self._drain_queues)

    def _console_write(self, text: str, tag: str = "") -> None:
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n", tag)
        if int(self.console.index("end-1c").split(".")[0]) > 4000:
            self.console.delete("1.0", "500.0")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        widgets = [self.btn_sweep]
        for slot in self.slots.values():
            widgets += [slot.btn_connect, slot.btn_scan]
        for w in widgets:
            try:
                w.configure(state="disabled" if busy else "normal")
            except tk.TclError:
                pass
        if not busy:
            for slot in self.slots.values():
                try:
                    slot.btn_connect.configure(
                        state="normal" if slot.enabled else "disabled")
                    slot.btn_scan.configure(
                        state="normal" if slot.enabled else "disabled")
                except tk.TclError:
                    pass
        if text:
            self._set_status(text)

    def _bg(self, fn, on_ok=None, on_err=None, busy_text: str = "") -> None:
        fut = self.worker.submit(fn)
        if busy_text:
            self._set_status(busy_text)

        def poll() -> None:
            if not fut.done():
                self.after(20, poll)
                return
            try:
                result = fut.result()
            except Exception as exc:                      # noqa: BLE001
                self._console_write(f"! {exc}", "err")
                self._set_status(str(exc).splitlines()[0])
                self._set_busy(False)
                if on_err:
                    on_err(exc)
                else:
                    messagebox.showerror("Instrument error", str(exc),
                                         parent=self)
                return
            if on_ok:
                on_ok(result)

        self.after(20, poll)

    # -- targets / sync group ---------------------------------------------

    def _targets(self) -> list[DeviceSlot]:
        """Connected units taking part in the current operation."""
        out = []
        a = self.slots["A"]
        if a.connected:
            out.append(a)
        b = self.slots["B"]
        if b.enabled and b.connected:
            out.append(b)
        return out

    def _targets_or_complain(self) -> list[DeviceSlot] | None:
        b = self.slots["B"]
        if b.enabled and not b.connected:
            messagebox.showwarning(
                "Device B not connected",
                "Device B is enabled but not connected. Connect it, or clear "
                "the Device B checkbox.", parent=self)
            return None
        targets = self._targets()
        if not targets:
            messagebox.showwarning("Not connected", "Connect an instrument "
                                                     "first.", parent=self)
            return None
        return targets

    def _group(self, mode: str | None = None) -> smu.SyncGroup:
        targets = self._targets()
        if not targets:
            raise smu.SmuError("No instrument connected")
        return smu.SyncGroup([s.inst for s in targets],
                             log=self._thread_log,
                             mode=mode or self.sync_mode)

    def _thread_log(self, line: str) -> None:
        self.logq.put(line)

    def _sync_mode_key(self) -> str:
        value = self.sync_mode_var.get()
        if value.startswith("Triggered"):
            return "trigger"
        if value.startswith("Parallel"):
            return "parallel"
        return "sequential"

    def _on_sync_mode(self, _event=None) -> None:
        self.sync_mode = self._sync_mode_key()
        self._console_write(f"--- sync mode: {self.sync_mode}", "sys")

    def _note_group(self, group: smu.SyncGroup) -> None:
        """Keep the UI in step with an automatic fallback."""
        if group.mode != self.sync_mode:
            self.sync_mode = group.mode
            self.sync_mode_var.set("Parallel" if group.mode == "parallel"
                                   else self.sync_mode_var.get())
            self._console_write("--- sync mode switched to parallel after "
                                "trigger fallback", "sys")

    def _update_skew(self, skew: float | None) -> None:
        if skew is None:
            self.skew_var.set("skew —")
        else:
            self.skew_var.set(f"skew {skew * 1e3:.2f} ms")

    # -- connection --------------------------------------------------------

    def _address_for(self, slot: DeviceSlot) -> str:
        text = slot.ip_var.get().strip()
        if not text:
            raise smu.SmuError("Enter the instrument's IP address.")
        transport = slot.transport_var.get()
        if transport == "VISA string":
            return text
        port = int(slot.port_var.get().strip() or smu.DEFAULT_PORT)
        return smu.build_address(text, port,
                                 "socket" if transport.startswith("Socket")
                                 else "vxi11")

    def _connect_slot(self, slot: DeviceSlot) -> None:
        if slot.connected:
            self._disconnect_slot(slot)
            return
        try:
            address = self._address_for(slot)
            timeout = int(float(slot.timeout_var.get().strip() or 10000))
        except ValueError:
            messagebox.showerror("Bad input", "Port and timeout must be "
                                              "numbers.", parent=self)
            return
        except smu.SmuError as exc:
            messagebox.showerror("No address", str(exc), parent=self)
            return

        other = self.slots["B" if slot.letter == "A" else "A"]
        if other.connected and other.inst.address == address:
            messagebox.showerror(
                "Same instrument twice",
                f"Device {other.letter} is already connected to {address}. "
                "Two sync slots need two different instruments.", parent=self)
            return

        slot.btn_connect.configure(text="Connecting…", state="disabled")
        self._set_status(f"Opening {slot.letter}: {address} …")

        def ok(idn: str) -> None:
            slot.set_connected(True, idn)
            self._console_write(f"--- device {slot.letter} connected: {idn}",
                                "sys")
            self._set_status(f"Device {slot.letter} connected")
            self._save_config()
            self._bg(slot.inst.output_state,
                     on_ok=lambda state: self._set_output_ui(slot, bool(state)),
                     on_err=lambda _e: self._set_output_ui(slot, False))

        def err(exc: Exception) -> None:
            slot.set_connected(False)
            slot.btn_connect.configure(text="Connect", state="normal")
            self._set_status(f"Device {slot.letter} connection failed")
            self._console_write(f"! [{slot.letter}] {exc}", "err")

        self._bg(lambda: slot.inst.open(address, timeout), on_ok=ok, on_err=err)

    def _connect_both(self) -> None:
        if not self.slots["B"].enabled:
            self.slots["B"].enabled_var.set(True)
            self._on_b_toggle()
        missing = []
        for slot in (self.slots["A"], self.slots["B"]):
            try:
                self._address_for(slot)
            except smu.SmuError:
                missing.append(slot.letter)
                continue
            if not slot.connected:
                self._connect_slot(slot)
        if missing:
            messagebox.showinfo(
                "Connect both",
                "No address for device " + ", ".join(missing)
                + ". Enter its IP address first.", parent=self)

    def _disconnect_slot(self, slot: DeviceSlot) -> None:
        self.polling = False
        self.btn_run.configure(text="Run continuous")
        slot.btn_connect.configure(text="Closing…", state="disabled")

        def done(_res=None) -> None:
            slot.set_connected(False)
            slot.reset_history()
            self._refresh_output_ui()
            self._set_status(f"Device {slot.letter} disconnected")

        self._bg(slot.inst.safe_shutdown, on_ok=done, on_err=lambda _e: done())

    def _disconnect_all(self) -> None:
        for slot in self.slots.values():
            if slot.connected:
                self._disconnect_slot(slot)

    def _on_b_toggle(self, silent: bool = False) -> None:
        enabled = bool(self.slots["B"].enabled_var.get())
        self.slots["B"].set_enabled(enabled)
        if not enabled and self.slots["B"].connected:
            self._disconnect_slot(self.slots["B"])
        for letter in ("A", "B"):
            block = self.read_blocks[letter]
            block.grid() if (letter == "A" or enabled) else block.grid_remove()
        if not silent:
            self._set_status(f"Device B {'enabled' if enabled else 'disabled'}")
        self._refresh_output_ui()

    def _scan(self, slot: DeviceSlot) -> None:
        ip = slot.ip_var.get().strip()
        if not ip:
            messagebox.showinfo("Scan LAN", "Type an IP address in the same "
                                            "subnet first, e.g. 192.168.1.10.",
                                parent=self)
            return
        port = int(slot.port_var.get() or smu.DEFAULT_PORT)
        self._set_busy(True, f"Scanning {ip.rsplit('.', 1)[0]}.0/24 on port "
                             f"{port} …")

        def ok(found: list[dict]) -> None:
            self._set_busy(False)
            if not found:
                self._set_status("No SCPI instruments answered on that port")
                messagebox.showinfo("Scan LAN", "No instrument answered on "
                                                "*IDN? in that /24.",
                                    parent=self)
                return
            self._console_write(f"{len(found)} instrument(s) found:", "out")
            for item in found:
                self._console_write(f"  {item['ip']}  {item['idn']}", "out")
            slot.ip_var.set(found[0]["ip"])
            self._set_status(f"Found {len(found)} instrument(s) — "
                             f"{found[0]['ip']} selected")

        self._bg(lambda: smu.scan_lan(
            ip, port, log=lambda s: self._thread_log("  " + s)), on_ok=ok)

    # -- source / output ---------------------------------------------------

    def _on_func_change(self, _event=None) -> None:
        source_voltage = self.src_func_var.get().startswith("Volt")
        self.level_unit.configure(text="V" if source_voltage else "A")
        self.compl_unit.configure(text="A" if source_voltage else "V")
        for letter in ("A", "B"):
            self.read_units[letter].configure(
                text="A" if source_voltage else "V")
        self.level_var.set("1.0" if source_voltage else "0.001")
        self.compl_var.set("0.01" if source_voltage else "10")
        self._style_axes(self.ax_sm, "time (s)",
                         "current (A)" if source_voltage else "voltage (V)")
        self.canvas_sm.draw_idle()

    def _read_settings(self) -> tuple[str, float, float]:
        source_voltage = self.src_func_var.get().startswith("Volt")
        try:
            level = float(self.level_var.get())
            compliance = float(self.compl_var.get())
        except ValueError as exc:
            raise smu.SmuError("Level and compliance must be numbers.") from exc
        return ("VOLT" if source_voltage else "CURR"), level, compliance

    def _apply_source(self) -> None:
        targets = self._targets_or_complain()
        if not targets:
            return
        try:
            func, level, compliance = self._read_settings()
        except smu.SmuError as exc:
            messagebox.showerror("Bad input", str(exc), parent=self)
            return
        auto = bool(self.autorange_var.get())
        group = self._group()

        def task():
            any_on = any(s.output_on for s in targets)
            if any_on and func != targets[0].src_applied:
                group.output(False)
            group.configure(func, level, compliance, auto_range=auto)
            for s in targets:
                s.src_applied = func
            return group.output_states()

        def ok(states: list[bool]) -> None:
            for slot, state in zip(targets, states):
                self._set_output_ui(slot, bool(state))
            self._note_group(group)
            self._set_status(f"Applied {func} {level:g} to "
                             f"{', '.join(s.letter for s in targets)} "
                             f"(compliance {compliance:g})")
            self._console_write(f"--- settings applied to "
                                f"{'+'.join(s.letter for s in targets)}: "
                                f"{func} {level:g}, compliance {compliance:g}",
                                "sys")

        self._bg(task, on_ok=ok, busy_text="Applying settings…")

    def _toggle_output(self) -> None:
        targets = self._targets_or_complain()
        if not targets:
            return
        any_on = any(s.output_on for s in targets)
        if any_on:
            group = self._group()

            def off() -> None:
                group.output(False)
                return True

            self._bg(off, on_ok=lambda _s: self._all_outputs_off(
                "Output OFF"), busy_text="Switching output off…")
            return

        try:
            func, level, compliance = self._read_settings()
        except smu.SmuError as exc:
            messagebox.showerror("Bad input", str(exc), parent=self)
            return
        auto = bool(self.autorange_var.get())
        group = self._group()

        def task():
            group.configure(func, level, compliance, auto_range=auto)
            for s in targets:
                s.src_applied = func
            group.output(True)
            return group.output_states()

        def ok(states: list[bool]) -> None:
            for slot, state in zip(targets, states):
                self._set_output_ui(slot, bool(state))
            self._note_group(group)
            self._set_status(f"Output ON ({'+'.join(s.letter for s in targets)})"
                             f" — {func} {level:g}, compliance {compliance:g}")

        self._bg(task, on_ok=ok, busy_text="Switching output on…")

    def _panic(self) -> None:
        targets = [s for s in self.slots.values() if s.connected]
        if not targets:
            self._set_status("Nothing connected — no output to switch off")
            return
        self.polling = False
        self.btn_run.configure(text="Run continuous")

        def task():
            for slot in targets:
                try:
                    slot.inst.output(False, verify=False)
                except Exception:
                    pass
            return True

        self._bg(task, on_ok=lambda _s: self._all_outputs_off("Output OFF (panic)"),
                 busy_text="Output off…")

    def _all_outputs_off(self, reason: str) -> None:
        for slot in self.slots.values():
            slot.output_on = False
        self._refresh_output_ui()
        self._set_status(reason)

    def _set_output_ui(self, slot: DeviceSlot, state: bool) -> None:
        slot.output_on = bool(state)
        self._refresh_output_ui()

    def _refresh_output_ui(self) -> None:
        on = [s.letter for s in self.slots.values() if s.output_on and s.connected]
        if on:
            self.out_btn.configure(text="OUTPUT  ON  (" + "+".join(on) + ")",
                                   bg=ON_BG)
            self.panic.configure(text="OUTPUT ON — click to stop", bg=ON_BG)
        else:
            self.out_btn.configure(text="OUTPUT  OFF", bg=OFF_BG)
            self.panic.configure(text="OUTPUT OFF", bg=OFF_BG)

    # -- measurement -------------------------------------------------------

    def _meas_func(self) -> str:
        return "CURR" if self.src_func_var.get().startswith("Volt") else "VOLT"

    def _update_readings(self, values: list[float], targets: list[DeviceSlot]) -> None:
        for slot in self.slots.values():
            if slot not in targets:
                self.read_vars[slot.letter].set("—")
        for slot, value in zip(targets, values):
            self.read_vars[slot.letter].set(f"{value:+.6e}")

    def _measure_once(self) -> None:
        targets = self._targets_or_complain()
        if not targets:
            return
        func = self._meas_func()
        group = self._group()

        def ok(result: tuple[list[float], float]) -> None:
            values, skew = result
            self._update_readings(values, targets)
            self._note_group(group)
            self._update_skew(skew if len(targets) > 1 else None)
            self._set_status("  ".join(f"{s.letter}={v:.6e}" for s, v
                                       in zip(targets, values)))

        self._bg(lambda: group.measure_synced(func), on_ok=ok,
                 busy_text="Measuring…")

    def _toggle_continuous(self) -> None:
        if self.polling:
            self.polling = False
            self.btn_run.configure(text="Run continuous")
            self._close_csv()
            self._set_status(f"Stopped — {self._total_samples()} samples")
            return
        targets = self._targets_or_complain()
        if not targets:
            return
        try:
            interval = max(30, int(float(self.interval_var.get())))
        except ValueError:
            messagebox.showerror("Bad input", "Interval must be a number of ms.",
                                 parent=self)
            return
        if self.csv_var.get() and not self._open_csv(targets):
            return
        for slot in targets:
            slot.reset_history()
        for letter in ("A", "B"):
            self._update_stats(self.slots[letter])
        self.t0 = time.time()
        self.polling = True
        self.btn_run.configure(text="Stop")
        self._set_status("Acquiring…")
        self.after(interval, self._cont_tick, interval, targets)

    def _cont_tick(self, interval: int, targets: list[DeviceSlot]) -> None:
        if not self.polling:
            return
        func = self._meas_func()
        group = self._group()
        fut = self.worker.submit(lambda: group.measure_synced(func))

        def poll() -> None:
            if not fut.done():
                self.after(15, poll)
                return
            try:
                values, skew = fut.result()
            except Exception as exc:                      # noqa: BLE001
                self.polling = False
                self.btn_run.configure(text="Run continuous")
                self._close_csv()
                self._console_write(f"! {exc}", "err")
                messagebox.showerror("Measurement failed", str(exc), parent=self)
                return
            self._note_group(group)
            self._update_skew(skew if len(targets) > 1 else None)
            self._add_point(targets, values, func)
            if self.polling:
                self.after(interval, self._cont_tick, interval, targets)

        self.after(15, poll)

    def _add_point(self, targets: list[DeviceSlot], values: list[float],
                   func: str) -> None:
        elapsed = time.time() - self.t0
        for slot, value in zip(targets, values):
            slot.t_data.append(elapsed)
            slot.y_data.append(value)
            slot.n += 1
            slot.y_min = min(slot.y_min, value)
            slot.y_max = max(slot.y_max, value)
            slot.y_sum += value
            self._update_stats(slot)
        self._update_readings(values, targets)
        if self.csv_writer is not None:
            row = [datetime.now().isoformat(timespec="milliseconds"),
                   f"{elapsed:.4f}"]
            row += [f"{v:.9e}" for v in values]
            self.csv_writer.writerow(row)
            self.csv_file.flush()
        if max(slot.n for slot in targets) % 2 == 0:
            self._plot_live()

    def _total_samples(self) -> int:
        return max([s.n for s in self.slots.values()] or [0])

    def _update_stats(self, slot: DeviceSlot) -> None:
        vars_ = self.stat_vars[slot.letter]
        vars_["n"].set(str(slot.n))
        if not slot.n:
            for key in ("min", "max", "mean"):
                vars_[key].set("—")
            return
        vars_["min"].set(f"{slot.y_min:.4e}")
        vars_["max"].set(f"{slot.y_max:.4e}")
        vars_["mean"].set(f"{slot.y_sum / slot.n:.4e}")

    def _plot_live(self) -> None:
        if not any(s.t_data for s in self.slots.values()):
            return
        ax = self.ax_sm
        ax.clear()
        unit = "A" if self._meas_func() == "CURR" else "V"
        self._style_axes(ax, "time (s)",
                         f"{'current' if unit == 'A' else 'voltage'} ({unit})")
        for slot in self._targets():
            if not slot.t_data:
                continue
            ax.plot(slot.t_data, slot.y_data, color=COLORS[slot.letter],
                    linewidth=1.2, label=f"Unit {slot.letter}")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        self.fig_sm.tight_layout()
        self.canvas_sm.draw_idle()

    def _clear_plot(self) -> None:
        for slot in self.slots.values():
            slot.reset_history()
            self._update_stats(slot)
        self.ax_sm.clear()
        unit = "A" if self._meas_func() == "CURR" else "V"
        self._style_axes(self.ax_sm, "time (s)",
                         f"{'current' if unit == 'A' else 'voltage'} ({unit})")
        self.canvas_sm.draw_idle()

    # -- CSV (continuous) --------------------------------------------------

    def _choose_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv", initialdir=os.getcwd(),
            initialfile=f"b2901b_{datetime.now():%Y%m%d_%H%M%S}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if path:
            self.csv_path_var.set(path)

    def _open_csv(self, targets: list[DeviceSlot]) -> bool:
        path = self.csv_path_var.get().strip()
        if not path:
            self._choose_csv()
            path = self.csv_path_var.get().strip()
            if not path:
                return False
        try:
            self.csv_file = open(path, "w", newline="", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("CSV", str(exc), parent=self)
            return False
        self.csv_writer = csv.writer(self.csv_file)
        unit = "A" if self._meas_func() == "CURR" else "V"
        header = ["timestamp", "elapsed_s"] + [
            f"{s.letter}_{unit}" for s in targets]
        self.csv_writer.writerow(header)
        self._set_status(f"Logging to {path}")
        return True

    def _close_csv(self) -> None:
        if self.csv_file is not None:
            try:
                self.csv_file.close()
            except OSError:
                pass
        self.csv_file = None
        self.csv_writer = None

    # -- sweep -------------------------------------------------------------

    def _on_sweep_func_change(self, _event=None) -> None:
        volt = self.sw_func_var.get().startswith("Volt")
        self.sw_compl_var.set("0.01" if volt else "10")
        self.sw_start_var.set("0" if volt else "0.0001")
        self.sw_stop_var.set("1" if volt else "0.001")
        self._style_axes(self.ax_sw,
                         "voltage (V)" if volt else "current (A)",
                         "current (A)" if volt else "voltage (V)")
        self.canvas_sw.draw_idle()

    def _run_sweep(self) -> None:
        if self.sweep_running:
            self.sweep_abort = True
            self.sw_progress.set("stopping…")
            return
        targets = self._targets_or_complain()
        if not targets:
            return
        volt = self.sw_func_var.get().startswith("Volt")
        func = "VOLT" if volt else "CURR"
        try:
            start = float(self.sw_start_var.get())
            stop = float(self.sw_stop_var.get())
            points = int(float(self.sw_points_var.get()))
            delay = max(0.0, float(self.sw_delay_var.get())) / 1000.0
            compliance = float(self.sw_compl_var.get())
        except ValueError:
            messagebox.showerror("Bad input", "Sweep fields must be numbers.",
                                 parent=self)
            return
        if points < 2:
            messagebox.showerror("Bad input", "Need at least 2 points.",
                                 parent=self)
            return

        self.sweep_running = True
        self.sweep_abort = False
        self.sweep_total = points
        self.sweep_letters = [s.letter for s in targets]
        self.sweep_data.clear()
        self.ax_sw.clear()
        self._style_axes(self.ax_sw, "voltage (V)" if volt else "current (A)",
                         "current (A)" if volt else "voltage (V)")
        self.canvas_sw.draw_idle()
        self.btn_sweep.configure(text="Stop sweep")
        self.btn_export.configure(state="disabled")
        self.sw_progress.set(f"running 0/{points}")
        self._set_busy(True, "Sweeping…")
        group = self._group()

        def task():
            group.configure(func, start, compliance, auto_range=True)
            group.output(True)
            try:
                data = group.sweep_synced(
                    func, start, stop, points, delay_s=delay,
                    on_point=lambda i, x, vals: self.pointq.put(
                        ("point", i, x, vals)),
                    should_stop=lambda: self.sweep_abort)
            finally:
                group.output(False)
            return data

        def ok(data: list[tuple[float, list[float]]]) -> None:
            self.sweep_running = False
            self._set_busy(False)
            # drop any point messages still queued for live plotting, then
            # take the authoritative result from the worker
            while True:
                try:
                    self.pointq.get_nowait()
                except queue.Empty:
                    break
            self.btn_sweep.configure(text="Run sweep")
            self.btn_export.configure(state="normal" if data else "disabled")
            for slot in targets:
                slot.output_on = False
            self._refresh_output_ui()
            self.sweep_data = list(data)
            self._plot_sweep()
            self._note_group(group)
            self.sw_progress.set(f"{'aborted' if self.sweep_abort else 'done'}"
                                 f" — {len(data)} points")
            self._set_status(f"Sweep finished, {len(data)} points")

        def err(exc: Exception) -> None:
            self.sweep_running = False
            self._set_busy(False)
            self.btn_sweep.configure(text="Run sweep")
            self.sw_progress.set("failed")
            for slot in targets:
                slot.output_on = False
            self._refresh_output_ui()
            messagebox.showerror("Sweep failed", str(exc), parent=self)

        self._bg(task, on_ok=ok, on_err=err)

    def _plot_sweep(self) -> None:
        if not self.sweep_data:
            return
        volt = self.sw_func_var.get().startswith("Volt")
        xs = [row[0] for row in self.sweep_data]
        ax = self.ax_sw
        ax.clear()
        self._style_axes(ax, "voltage (V)" if volt else "current (A)",
                         "current (A)" if volt else "voltage (V)")
        for index, letter in enumerate(self.sweep_letters):
            ys = [row[1][index] for row in self.sweep_data
                  if len(row[1]) > index]
            ax.plot(xs[:len(ys)], ys, color=COLORS.get(letter, ACCENT),
                    linewidth=1.4, marker="o", markersize=3,
                    label=f"Unit {letter}")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        self.fig_sw.tight_layout()
        self.canvas_sw.draw_idle()

    def _export_sweep(self) -> None:
        if not self.sweep_data:
            return
        volt = self.sw_func_var.get().startswith("Volt")
        letters = getattr(self, "sweep_letters", ["A"])
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv", initialdir=os.getcwd(),
            initialfile=f"sweep_{datetime.now():%Y%m%d_%H%M%S}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if not path:
            return
        unit = "A" if volt else "V"
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["source_V" if volt else "source_A"]
                           + [f"{lt}_{unit}" for lt in letters])
                for row in self.sweep_data:
                    w.writerow([f"{row[0]:.9e}"]
                               + [f"{v:.9e}" for v in row[1]])
        except OSError as exc:
            messagebox.showerror("CSV", str(exc), parent=self)
            return
        self._set_status(f"Sweep exported to {path}")

    # -- pulses ------------------------------------------------------------

    def _on_pulse_func_change(self, _event=None) -> None:
        volt = self.pu_func_var.get().startswith("Volt")
        self.pu_units["base"].configure(text="V" if volt else "A")
        self.pu_units["peak"].configure(text="V" if volt else "A")
        self.pu_units["compl"].configure(text="A" if volt else "V")
        self.pu_peak_var.set("1.0" if volt else "0.001")
        self.pu_compl_var.set("0.01" if volt else "10")
        self._style_axes(self.ax_pu, "pulse number",
                         "current (A)" if volt else "voltage (V)")
        self.canvas_pu.draw_idle()

    def _pulse_params(self) -> dict:
        volt = self.pu_func_var.get().startswith("Volt")
        try:
            params = {
                "sour_func": "VOLT" if volt else "CURR",
                "base": float(self.pu_base_var.get()),
                "peak": float(self.pu_peak_var.get()),
                "width_s": float(self.pu_width_var.get()) * 1e-6,
                "period_s": float(self.pu_period_var.get()) * 1e-3,
                "count": int(float(self.pu_count_var.get())),
                "pulse_delay_s": float(self.pu_delay_var.get()) * 1e-6,
                "aperture_s": float(self.pu_aper_var.get()) * 1e-6,
                "compliance": float(self.pu_compl_var.get()),
            }
        except ValueError as exc:
            raise smu.SmuError("Pulse fields must be numbers.") from exc

        if params["width_s"] <= 0:
            raise smu.SmuError("Pulse width must be positive")
        if params["aperture_s"] <= 0 or params["aperture_s"] > params["width_s"]:
            raise smu.SmuError("Aperture must be > 0 and no wider than the pulse")
        if params["pulse_delay_s"] + params["width_s"] >= params["period_s"]:
            raise smu.SmuError("Pulse delay + width must fit inside the period")
        if params["count"] < 1:
            raise smu.SmuError("Count must be at least 1")
        return params

    def _run_pulses(self) -> None:
        if self.pulse_running:
            self.pulse_abort = True
            self.pu_progress.set("stopping…")
            return
        targets = self._targets_or_complain()
        if not targets:
            return
        try:
            params = self._pulse_params()
        except smu.SmuError as exc:
            messagebox.showerror("Bad pulse settings", str(exc), parent=self)
            return

        group = self._group()
        meas = "CURR" if params["sour_func"] == "VOLT" else "VOLT"
        outoff = bool(self.pu_outoff_var.get())
        self.pulse_running = True
        self.pulse_abort = False
        self.pulse_letters = [s.letter for s in targets]
        self.pulse_data = {}
        self.pulse_deadline = time.time() + params["count"] * params["period_s"]
        self.btn_pulse.configure(text="Stop pulse train")
        self.btn_pulse_export.configure(state="disabled")
        self._set_busy(True)
        total = params["count"] * params["period_s"]
        self.pu_progress.set(f"pulsing {params['count']} × "
                             f"{params['width_s'] * 1e6:.0f} µs @ "
                             f"{params['period_s'] * 1e3:.3g} ms "
                             f"({total:.2f} s)")
        self.pu_summary.set(
            f"{'V' if params['sour_func'] == 'VOLT' else 'A'}: base "
            f"{params['base']:g} → peak {params['peak']:g}, "
            f"{params['width_s'] * 1e6:.0f} µs wide, "
            f"{params['period_s'] * 1e3:.3g} ms period, "
            f"aperture {params['aperture_s'] * 1e6:.0f} µs, "
            f"{params['count']} pulses")
        self._pulse_tick()

        def task():
            group.pulse_configure(
                params["sour_func"], base=params["base"],
                peak=params["peak"], width_s=params["width_s"],
                period_s=params["period_s"],
                pulse_delay_s=params["pulse_delay_s"],
                aperture_s=params["aperture_s"],
                compliance=params["compliance"], count=params["count"])
            try:
                arrays, skew = group.pulse_train(
                    params["count"], params["period_s"], meas,
                    should_stop=lambda: self.pulse_abort)
            finally:
                try:
                    group.pulse_cleanup(outoff)
                except Exception:
                    pass
            return arrays, skew

        def ok(result) -> None:
            arrays, skew = result
            self.pulse_running = False
            self._set_busy(False)
            self.btn_pulse.configure(text="Run pulse train")
            self.btn_pulse_export.configure(
                state="normal" if any(arrays) else "disabled")
            self._update_skew(skew if len(targets) > 1 else None)
            for slot in targets:
                slot.output_on = not outoff
            self._refresh_output_ui()
            self.pulse_data = {s.letter: list(arr)
                               for s, arr in zip(targets, arrays)}
            self._plot_pulses()
            n = max([len(a) for a in arrays] or [0])
            self.pu_progress.set(f"{n} pulses captured"
                                 + (" — stopped early" if self.pulse_abort else ""))
            self._set_status(f"Pulse train done: {n} pulses on "
                             f"{'+'.join(s.letter for s in targets)}")

        def err(exc: Exception) -> None:
            self.pulse_running = False
            self._set_busy(False)
            self.btn_pulse.configure(text="Run pulse train")
            self.pu_progress.set("failed")
            for slot in targets:
                slot.output_on = False
            self._refresh_output_ui()
            messagebox.showerror("Pulse train failed", str(exc), parent=self)

        self._bg(task, on_ok=ok, on_err=err)

    def _pulse_tick(self) -> None:
        if not self.pulse_running:
            return
        remaining = self.pulse_deadline - time.time()
        if remaining > 0:
            self.pu_progress.set(f"pulsing… {remaining:.1f} s left")
        else:
            self.pu_progress.set("reading acquisition buffer…")
        self.after(200, self._pulse_tick)

    def _plot_pulses(self) -> None:
        if not self.pulse_data:
            return
        volt = self.pu_func_var.get().startswith("Volt")
        ax = self.ax_pu
        ax.clear()
        self._style_axes(ax, "pulse number",
                         "current (A)" if volt else "voltage (V)")
        for letter in self.pulse_letters:
            ys = self.pulse_data.get(letter) or []
            if not ys:
                continue
            ax.plot(range(1, len(ys) + 1), ys, color=COLORS.get(letter, ACCENT),
                    linewidth=1.2, marker="o", markersize=3.5,
                    label=f"Unit {letter}")
        ax.legend(loc="best", fontsize=8, frameon=False)
        self.fig_pu.tight_layout()
        self.canvas_pu.draw_idle()
        for letter in ("A", "B"):
            values = self.pulse_data.get(letter) or []
            vars_ = self.pu_stat_vars[letter]
            if not values:
                for key in ("n", "mean", "min", "max", "last"):
                    vars_[key].set("—")
                continue
            vars_["n"].set(str(len(values)))
            vars_["mean"].set(f"{sum(values) / len(values):.4e}")
            vars_["min"].set(f"{min(values):.4e}")
            vars_["max"].set(f"{max(values):.4e}")
            vars_["last"].set(f"{values[-1]:.4e}")

    def _export_pulses(self) -> None:
        if not self.pulse_data:
            return
        volt = self.pu_func_var.get().startswith("Volt")
        letters = [lt for lt in self.pulse_letters if self.pulse_data.get(lt)]
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv", initialdir=os.getcwd(),
            initialfile=f"pulses_{datetime.now():%Y%m%d_%H%M%S}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if not path:
            return
        unit = "A" if volt else "V"
        count = max(len(self.pulse_data[lt]) for lt in letters)
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["pulse"] + [f"{lt}_{unit}" for lt in letters])
                for i in range(count):
                    row = [i + 1]
                    for lt in letters:
                        values = self.pulse_data[lt]
                        row.append(f"{values[i]:.9e}" if i < len(values) else "")
                    w.writerow(row)
        except OSError as exc:
            messagebox.showerror("CSV", str(exc), parent=self)
            return
        self._set_status(f"Pulse data exported to {path}")

    # -- SCPI console ------------------------------------------------------

    def _scpi_slots(self) -> list[DeviceSlot]:
        target = self.scpi_target_var.get()
        if target == "A":
            letters = ["A"]
        elif target == "B":
            letters = ["B"]
        else:
            letters = [s.letter for s in self._targets()] or ["A"]
        return [self.slots[lt] for lt in letters]

    def _send_scpi(self, command: str | None = None) -> None:
        cmd = (command if command is not None else self.cmd_var.get()).strip()
        if not cmd:
            return
        slots = self._scpi_slots()
        if not any(s.connected for s in slots):
            messagebox.showwarning("Not connected", "Connect an instrument "
                                                     "first.", parent=self)
            return
        if command is None:
            self.history.append(cmd)
            self.hist_idx = len(self.history)
            self.cmd_var.set("")

        is_query = cmd.endswith("?")

        def task():
            results = []
            for slot in slots:
                if not slot.connected:
                    continue
                try:
                    results.append((slot.letter,
                                    slot.inst.query(cmd) if is_query
                                    else slot.inst.write(cmd)))
                except Exception as exc:                  # noqa: BLE001
                    results.append((slot.letter, f"! {exc}"))
            return results

        def ok(results: list[tuple[str, object]]) -> None:
            for letter, value in results:
                if isinstance(value, str) and value.startswith("! "):
                    self._console_write(f"! [{letter}] {value[2:]}", "err")
                elif is_query:
                    self._console_write(f"[{letter}] < {value}", "out")

        self._bg(task, on_ok=ok)

    def _history_up(self, _event=None) -> str:
        if not self.history:
            return "break"
        self.hist_idx = max(0, self.hist_idx - 1)
        self.cmd_var.set(self.history[self.hist_idx])
        return "break"

    def _history_down(self, _event=None) -> str:
        if not self.history:
            return "break"
        self.hist_idx = min(len(self.history), self.hist_idx + 1)
        self.cmd_var.set(self.history[self.hist_idx]
                         if self.hist_idx < len(self.history) else "")
        return "break"

    # -- config / shutdown -------------------------------------------------

    def _load_config(self) -> None:
        try:
            cfg = json.loads(CONFIG.read_text())
        except (OSError, ValueError):
            return
        for letter in ("A", "B"):
            data = cfg.get(letter.lower())
            if not data:
                continue
            slot = self.slots[letter]
            slot.ip_var.set(data.get("address", ""))
            slot.port_var.set(str(data.get("port", smu.DEFAULT_PORT)))
            slot.timeout_var.set(str(data.get("timeout", 10000)))
            slot.transport_var.set(data.get("transport", "Socket (5025)"))
        if cfg.get("b_enabled"):
            self.slots["B"].enabled_var.set(True)
            self._on_b_toggle()
        self.sync_mode = cfg.get("sync_mode", "trigger")
        self.sync_mode_var.set({"trigger": "Triggered (BUS)",
                                "parallel": "Parallel",
                                "sequential": "Sequential (A then B)"
                                }.get(self.sync_mode, "Triggered (BUS)"))

    def _save_config(self) -> None:
        def data(slot: DeviceSlot) -> dict:
            return {"address": slot.ip_var.get().strip(),
                    "port": slot.port_var.get().strip(),
                    "timeout": slot.timeout_var.get().strip(),
                    "transport": slot.transport_var.get()}

        cfg = {"a": data(self.slots["A"]), "b": data(self.slots["B"]),
               "b_enabled": bool(self.slots["B"].enabled_var.get()),
               "sync_mode": self.sync_mode}
        try:
            CONFIG.write_text(json.dumps(cfg, indent=2))
        except OSError:
            pass

    def _on_close(self) -> None:
        self.polling = False
        self.pulse_abort = True
        self._close_csv()
        for slot in self.slots.values():
            if slot.connected:
                try:
                    self.worker.submit(slot.inst.safe_shutdown).result(timeout=4)
                except Exception:
                    pass
        self.worker.shutdown()
        self.destroy()


# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Keysight B2901B LAN GUI")
    parser.add_argument("--address", help="IP address for device A")
    parser.add_argument("--address-b", help="IP address for device B (sync)")
    parser.add_argument("--port", type=int, default=smu.DEFAULT_PORT,
                        help="TCP port for device A")
    parser.add_argument("--port-b", type=int, default=smu.DEFAULT_PORT,
                        help="TCP port for device B")
    parser.add_argument("--smoke", type=float, default=0,
                        help="close automatically after N seconds (testing)")
    args = parser.parse_args()

    app = App(address_a=args.address, address_b=args.address_b)
    app.slots["A"].port_var.set(str(args.port))
    app.slots["B"].port_var.set(str(args.port_b))
    if args.smoke:
        app.after(int(args.smoke * 1000), app._on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
