import { useState } from "react";
import { Panel } from "./ui";
import { downloadCsv, timestampSlug } from "../lib/csv";
import { fileSize, si } from "../lib/format";
import type { AppApi } from "../hooks/useApp";

export default function DataTab({ app }: { app: AppApi }) {
  const { state, buffer, sweep, pulse, actions } = app;
  const [name, setName] = useState("");

  if (!state) return null;
  const session = state.session;

  const exportBuffer = () => {
    const rows: (string | number | null)[][] = [
      ["device", "timestamp", "volts", "amps", "ohms", "watts"],
    ];
    (["A", "B"] as const).forEach((letter) => {
      buffer[letter].forEach((reading) => {
        rows.push([
          letter,
          reading.time,
          reading.volts,
          reading.amps,
          reading.ohms,
          reading.watts,
        ]);
      });
    });
    downloadCsv(`live-buffer-${timestampSlug()}.csv`, rows);
  };

  const counts = session?.counts;

  return (
    <div className="tab-body data-tab">
      <div className="data-grid">
        <Panel title="Session" subtitle="Everything measured lands here automatically" className="data-card">
          <div className="session-hero">
            <div>
              <span className="kicker">session id</span>
              <strong>{session?.id ?? "—"}</strong>
            </div>
            <div>
              <span className="kicker">created</span>
              <strong>{session ? new Date(session.created).toLocaleString() : "—"}</strong>
            </div>
          </div>

          <div className="session-counts">
            <div>
              <em>measurements</em>
              <strong>{counts?.measurements ?? 0}</strong>
            </div>
            <div>
              <em>sweep points</em>
              <strong>{counts?.sweep_points ?? 0}</strong>
            </div>
            <div>
              <em>pulse samples</em>
              <strong>{counts?.pulse_points ?? 0}</strong>
            </div>
          </div>

          <div className="session-new">
            <input
              placeholder="optional session name (e.g. device-17 IV)"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                actions.newSession(name.trim());
                setName("");
              }}
            >
              New session
            </button>
          </div>

          <p className="hint-text">
            Starting a new session leaves the old files on disk and opens a fresh set of CSVs.
          </p>
        </Panel>

        <Panel title="Files" subtitle={session?.dir ?? ""} className="data-card">
          <div className="file-list">
            {session?.files.map((file) => (
              <div className="file-row" key={file.name}>
                <div>
                  <strong>{file.name}</strong>
                  <em>{fileSize(file.size)}</em>
                </div>
                <span className="file-hint">{describe(file.name)}</span>
              </div>
            ))}
          </div>

          <div className="download-grid">
            <button
              type="button"
              className="btn primary"
              onClick={() =>
                actions.download(
                  "all",
                  `keysight-session-${session?.id ?? "unknown"}.zip`,
                )
              }
            >
              Download everything (.zip)
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => actions.download("measurements", "measurements.csv")}
            >
              measurements.csv
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => actions.download("sweeps", "sweeps.csv")}
            >
              sweeps.csv
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => actions.download("pulses", "pulses.csv")}
            >
              pulses.csv
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => actions.download("events", "events.log")}
            >
              events.log
            </button>
          </div>
        </Panel>

        <Panel title="Export from the browser" subtitle="In-memory data, no server round trip" className="data-card">
          <div className="download-grid">
            <button type="button" className="btn ghost" onClick={exportBuffer}>
              Live buffer ({buffer.A.length + buffer.B.length} samples)
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={!sweep.runId}
              onClick={() => {
                const rows: (string | number | null)[][] = [
                  ["device", "index", "x", "volts", "amps", "ohms", "watts"],
                ];
                (["A", "B"] as const).forEach((letter) => {
                  sweep.rows[letter].forEach((row) => {
                    rows.push([letter, row.index, row.x, row.volts, row.amps, row.ohms, row.watts]);
                  });
                });
                downloadCsv(`sweep-${sweep.runId}.csv`, rows);
              }}
            >
              Current sweep ({sweep.rows.A.length + sweep.rows.B.length} points)
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={!pulse.runId}
              onClick={() => {
                const rows: (string | number | null)[][] = [
                  ["device", "index", "volts", "amps", "ohms", "measured"],
                ];
                (["A", "B"] as const).forEach((letter) => {
                  pulse.rows[letter].forEach((row) => {
                    rows.push([letter, row.index, row.volts, row.amps, row.ohms, row.measured]);
                  });
                });
                downloadCsv(`pulse-${pulse.runId}.csv`, rows);
              }}
            >
              Current pulse train ({pulse.rows.A.length + pulse.rows.B.length} samples)
            </button>
          </div>

          <div className="format-table">
            <div className="format-row format-head">
              <span>file</span>
              <span>columns</span>
            </div>
            <div className="format-row">
              <span>measurements.csv</span>
              <span>timestamp, iso, device, volts, amps, ohms, watts</span>
            </div>
            <div className="format-row">
              <span>sweeps.csv</span>
              <span>run_id, iso, index, device, source_value, volts, amps, ohms, watts</span>
            </div>
            <div className="format-row">
              <span>pulses.csv</span>
              <span>run_id, iso, device, index, source_value, volts, amps, ohms, watts</span>
            </div>
          </div>
        </Panel>

        <Panel title="Backend" subtitle="Where the data lives on this machine" className="data-card">
          <div className="kv-grid wide">
            <span>
              <em>data dir</em>
              <code>{state.server.data_dir}</code>
            </span>
            <span>
              <em>uptime</em>
              {Math.floor((Date.now() / 1000 - state.server.started) / 60)} min
            </span>
            <span>
              <em>last reading A</em> {si(state.devices.A.last?.amps, "A")}
            </span>
            <span>
              <em>last reading B</em> {si(state.devices.B.last?.amps, "A")}
            </span>
            <span>
              <em>sync mode</em> {state.settings.sync_mode}
            </span>
            <span>
              <em>wiring</em> {state.settings.wire === "4W" ? "4-wire" : "2-wire"}
            </span>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function describe(name: string): string {
  if (name === "measurements.csv") return "every spot / streamed reading";
  if (name === "sweeps.csv") return "one row per point per unit";
  if (name === "pulses.csv") return "one row per pulse per unit";
  if (name === "events.log") return "connect, configure, run history";
  if (name === "meta.json") return "session summary";
  return "";
}
