#!/usr/bin/env python3
"""gui_smoke.py -- drive the real Tk GUI end-to-end against two mock units.

Starts nothing itself: run the mocks first.

    python3 mock_b2901b.py --port 5025 --load 100 &
    python3 mock_b2901b.py --port 5026 --load 200 &
    python3 gui_smoke.py

It builds the actual App, connects device A and B, applies settings, switches
the output on, measures, runs a synchronised sweep and switches everything
off, asserting on the widget state at each step.  Exit code 0 = all good.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk

import keysight_gui as kg

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"   ({detail})" if detail else ""), flush=True)


def main() -> int:
    # never let a modal dialog block an automated run
    def note(kind):
        def handler(title="", message="", **_kw):
            print(f"DIALOG {kind}: {title} :: {message}", flush=True)
            RESULTS.append((False, f"dialog {kind}: {title}", str(message)))
        return handler

    kg.messagebox.showerror = note("error")
    kg.messagebox.showwarning = note("warning")
    kg.messagebox.showinfo = note("info")

    app = kg.App()
    app.slots["A"].ip_var.set("127.0.0.1")
    app.slots["A"].port_var.set("5025")
    app.slots["B"].ip_var.set("127.0.0.1")
    app.slots["B"].port_var.set("5026")
    app.slots["B"].enabled_var.set(True)
    app._on_b_toggle()

    state = {"step": 0, "failed": False}

    def wait(pred, then, deadline: float = 8.0, what: str = ""):
        limit = time.time() + deadline

        def tick():
            if pred():
                then()
            elif time.time() > limit:
                check(f"timeout waiting for {what or 'condition'}", False,
                      app.status_var.get())
                state["failed"] = True
                finish()
            else:
                app.after(50, tick)

        app.after(50, tick)

    def finish():
        try:
            app._on_close()
        except Exception:
            pass

    def step_sweep():
        app.sw_func_var.set("Voltage")
        app.sw_start_var.set("0")
        app.sw_stop_var.set("1")
        app.sw_points_var.set("5")
        app.sw_delay_var.set("0")
        app.sw_compl_var.set("0.02")
        app._run_sweep()
        check("sweep started", app.sweep_running, str(app.sweep_running))
        wait(lambda: not app.sweep_running and app.sweep_data,
             after_sweep, what="sweep to finish")

    def after_sweep():
        rows = app.sweep_data
        check("sweep produced 5 points", len(rows) == 5, str(len(rows)))
        last = rows[-1][1] if rows else []
        check("sweep last point 10 mA / 5 mA",
              len(last) == 2 and abs(last[0] - 0.01) < 1e-9
              and abs(last[1] - 0.005) < 1e-9, str(last))
        check("outputs off after sweep",
              not any(s.output_on for s in app.slots.values()))
        check("sweep csv export enabled",
              str(app.btn_export["state"]) == "normal")
        app._panic()
        wait(lambda: not any(s.output_on for s in app.slots.values()),
             after_panic, what="panic")

    def after_panic():
        check("panic leaves outputs off",
              not any(s.output_on for s in app.slots.values()))
        check("sync mode still trigger", app.sync_mode == "trigger",
              app.sync_mode)
        check("skew label populated", "ms" in app.skew_var.get(),
              app.skew_var.get())
        step_pulses()

    def step_pulses():
        app.pu_func_var.set("Voltage")
        app.pu_base_var.set("0")
        app.pu_peak_var.set("0.5")
        app.pu_width_var.set("1000")
        app.pu_period_var.set("5")
        app.pu_count_var.set("6")
        app.pu_delay_var.set("500")
        app.pu_aper_var.set("100")
        app.pu_compl_var.set("0.02")
        app._run_pulses()
        check("pulse run started", app.pulse_running, str(app.pulse_running))
        wait(lambda: not app.pulse_running and app.pulse_data, after_pulses,
             deadline=12, what="pulse train")

    def after_pulses():
        data = app.pulse_data
        a_vals = data.get("A", [])
        b_vals = data.get("B", [])
        check("pulse: 6 results on A", len(a_vals) == 6, str(len(a_vals)))
        check("pulse: 6 results on B", len(b_vals) == 6, str(len(b_vals)))
        check("pulse: A peak current 5 mA",
              bool(a_vals) and abs(a_vals[0] - 0.005) < 1e-9,
              f"{a_vals[0]:.3e}" if a_vals else "none")
        check("pulse: B peak current 2.5 mA",
              bool(b_vals) and abs(b_vals[0] - 0.0025) < 1e-9,
              f"{b_vals[0]:.3e}" if b_vals else "none")
        check("pulse: outputs off afterwards",
              not any(s.output_on for s in app.slots.values()))
        check("pulse: export button enabled",
              str(app.btn_pulse_export["state"]) == "normal")
        check("pulse: stats filled",
              app.pu_stat_vars["A"]["mean"].get() != "—",
              app.pu_stat_vars["A"]["mean"].get())
        print(f"\nsync skew: {app.skew_var.get()}")
        print(f"pulse summary: {app.pu_summary.get()}")
        finish()

    def step_measure():
        app._measure_once()
        wait(lambda: app.read_vars["B"].get() != "—", after_measure,
             what="measurement")

    def after_measure():
        a = app.read_vars["A"].get()
        b = app.read_vars["B"].get()
        check("reading A = +5.000000e-03", a.startswith("+5.000000e-03"), a)
        check("reading B = +2.500000e-03", b.startswith("+2.500000e-03"), b)

        app._toggle_continuous()
        wait(lambda: app.slots["A"].n >= 3 and app.slots["B"].n >= 3,
             after_continuous, what="continuous samples")

    def after_continuous():
        check("continuous collected samples on both",
              app.slots["A"].n >= 3 and app.slots["B"].n >= 3,
              f"A={app.slots['A'].n} B={app.slots['B'].n}")
        check("A statistics updated",
              app.stat_vars["A"]["mean"].get() != "—",
              app.stat_vars["A"]["mean"].get())
        app._toggle_continuous()
        step_sweep()

    def step_output():
        app._toggle_output()
        wait(lambda: app.slots["A"].output_on and app.slots["B"].output_on,
             step_measure, what="outputs on")

    def step_apply():
        app.level_var.set("0.5")
        app.compl_var.set("0.02")
        app._apply_source()
        wait(lambda: "Applied" in app.status_var.get(), step_output,
             what="settings applied")

    def step_connect():
        app._connect_both()
        wait(lambda: app.slots["A"].connected and app.slots["B"].connected,
             after_connect, what="both connections")

    def after_connect():
        check("device A identity shown",
              "B2901B" in app.slots["A"].idn_var.get(),
              app.slots["A"].idn_var.get())
        check("device B identity shown",
              "B2901B" in app.slots["B"].idn_var.get(),
              app.slots["B"].idn_var.get())
        check("two targets in sync group", len(app._targets()) == 2,
              str([s.letter for s in app._targets()]))
        check("device B reading block visible",
              app.read_blocks["B"].winfo_manager() == "grid",
              app.read_blocks["B"].winfo_manager())
        step_apply()

    app.after(400, step_connect)
    app.after(45000, lambda: (check("hard timeout", False, "45 s"), finish()))
    app.mainloop()

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} GUI checks passed")
    return 0 if passed == len(RESULTS) and not state["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
