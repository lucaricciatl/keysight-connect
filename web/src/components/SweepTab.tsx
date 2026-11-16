import { useEffect, useMemo, useState } from "react";
import NumberField from "./NumberField";
import Plot from "./Plot";
import { Panel, Segmented, Toggle } from "./ui";
import { downloadCsv, timestampSlug } from "../lib/csv";
import { CURRENT_UNITS, si, siParts, VOLTAGE_UNITS } from "../lib/format";
import type { AppApi } from "../hooks/useApp";
import type { SourceFunc, WireMode } from "../lib/types";

const PRESETS: { label: string; start: number; stop: number; points: number; source: SourceFunc }[] = [
  { label: "0 → 1 V", start: 0, stop: 1, points: 41, source: "VOLT" },
  { label: "0 → 5 V", start: 0, stop: 5, points: 51, source: "VOLT" },
  { label: "−1 → 1 V", start: -1, stop: 1, points: 41, source: "VOLT" },
  { label: "0 → 10 mA", start: 0, stop: 10e-3, points: 41, source: "CURR" },
];

export default function SweepTab({ app }: { app: AppApi }) {
  const { state, sweep, actions, busy } = app;
  const [source, setSource] = useState<SourceFunc>("VOLT");
  const [start, setStart] = useState(0);
  const [stop, setStop] = useState(1);
  const [points, setPoints] = useState(41);
  const [delayMs, setDelayMs] = useState(20);
  const [compliance, setCompliance] = useState(1e-3);
  const [autoRange, setAutoRange] = useState(true);
  const [wire, setWire] = useState<WireMode>("2W");
  const [outputOff, setOutputOff] = useState(true);
  const [yMetric, setYMetric] = useState<"measured" | "ohms">("measured");
  const [logY, setLogY] = useState(false);

  useEffect(() => {
    if (state?.settings.wire) setWire(state.settings.wire);
  }, [state?.settings.wire]);

  const devices = useMemo(
    () =>
      (["A", "B"] as const).filter(
        (letter) => state?.devices[letter].connected && sweep.rows[letter].length > 0,
      ),
    [state?.devices.A.connected, state?.devices.B.connected, sweep.rows],
  );

  const activeSource: SourceFunc = sweep.runId ? sweep.source : source;
  const measuredIsCurrent = activeSource === "VOLT";

  const series = useMemo(
    () =>
      devices.map((letter) => ({
        name: `Device ${letter}`,
        color: letter === "A" ? "var(--dev-a)" : "var(--dev-b)",
        points: sweep.rows[letter].map((row) => ({
          x: row.x,
          y:
            yMetric === "ohms"
              ? row.ohms ?? Number.NaN
              : measuredIsCurrent
                ? row.amps
                : row.volts,
        })),
      })),
    [devices, sweep.rows, measuredIsCurrent, yMetric],
  );

  if (!state) return null;

  const connected = state.devices.A.connected || state.devices.B.connected;
  const running = sweep.running && state.job.state === "running";

  const run = () =>
    actions.runSweep({
      source,
      start,
      stop,
      points,
      delay_ms: delayMs,
      compliance,
      auto_range: autoRange,
      output_off: outputOff,
      wire,
      sync_mode: state.settings.sync_mode,
    });

  const exportRun = () => {
    if (!sweep.runId) return;
    const rows: (string | number | null)[][] = [
      ["device", "index", "source_value", "volts", "amps", "ohms", "watts"],
    ];
    (["A", "B"] as const).forEach((letter) => {
      sweep.rows[letter].forEach((row) => {
        rows.push([letter, row.index, row.x, row.volts, row.amps, row.ohms, row.watts]);
      });
    });
    downloadCsv(`sweep-${sweep.runId}-${timestampSlug()}.csv`, rows);
  };

  const yUnit = yMetric === "ohms" ? "Ω" : measuredIsCurrent ? "A" : "V";

  return (
    <div className="tab-body sweep-tab">
      <Panel
        title="I–V sweep"
        subtitle="Instrument-paced linear ramp, both units in lockstep"
        className="setup-card"
        actions={
          <div className="row-actions">
            {running ? (
              <button type="button" className="btn danger" onClick={() => actions.stopJob()}>
                Stop
              </button>
            ) : null}
            <button
              type="button"
              className="btn primary"
              disabled={!connected || running || busy === "sweep"}
              onClick={run}
            >
              Run sweep
            </button>
          </div>
        }
      >
        <Segmented
          value={source}
          onChange={(next) => {
            setSource(next);
            setStart(next === "VOLT" ? 0 : 0);
            setStop(next === "VOLT" ? 1 : 1e-3);
            setCompliance(next === "VOLT" ? 1e-3 : 1);
          }}
          options={[
            { value: "VOLT", label: "Source V" },
            { value: "CURR", label: "Source I" },
          ]}
        />

        <div className="form-grid two">
          <NumberField
            label="Start"
            value={start}
            onChange={setStart}
            units={source === "VOLT" ? VOLTAGE_UNITS : CURRENT_UNITS}
          />
          <NumberField
            label="Stop"
            value={stop}
            onChange={setStop}
            units={source === "VOLT" ? VOLTAGE_UNITS : CURRENT_UNITS}
          />
          <NumberField label="Points" value={points} onChange={setPoints} units={[{ label: "", scale: 1 }]} />
          <NumberField
            label="Dwell per point"
            value={delayMs}
            onChange={setDelayMs}
            units={[
              { label: "ms", scale: 1 },
              { label: "s", scale: 1000 },
            ]}
          />
          <NumberField
            label="Compliance"
            value={compliance}
            onChange={setCompliance}
            units={source === "VOLT" ? CURRENT_UNITS : VOLTAGE_UNITS}
          />
        </div>

        <div className="toggle-row">
          <Toggle checked={autoRange} onChange={setAutoRange} label="Auto range" />
          <Toggle checked={outputOff} onChange={setOutputOff} label="Output off after" />
        </div>

        <div className="toggle-row">
          <div className="wire-select">
            <span className="wire-select-label">Wiring</span>
            <Segmented
              value={wire}
              onChange={setWire}
              options={[
                { value: "2W", label: "2-wire" },
                { value: "4W", label: "4-wire" },
              ]}
            />
          </div>
        </div>

        <div className="preset-row">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className="chip-button"
              onClick={() => {
                setSource(preset.source);
                setStart(preset.start);
                setStop(preset.stop);
                setPoints(preset.points);
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>

        {sweep.params ? (
          <div className="sweep-summary">
            <span>
              <em>range</em> {si(sweep.params.start, source === "VOLT" ? "V" : "A")} →{" "}
              {si(sweep.params.stop, source === "VOLT" ? "V" : "A")}
            </span>
            <span>
              <em>points</em> {sweep.params.points}
            </span>
            <span>
              <em>state</em>{" "}
              {running
                ? `${state.job.progress}/${state.job.total}`
                : sweep.aborted
                  ? "stopped"
                  : "complete"}
            </span>
          </div>
        ) : null}

        {running ? (
          <div className="progress">
            <div
              className="progress-fill"
              style={{
                width: `${Math.min(100, (state.job.progress / Math.max(state.job.total, 1)) * 100)}%`,
              }}
            />
          </div>
        ) : null}

        <p className="hint-text">
          Both outputs switch on for the sweep{outputOff ? " and off when it ends" : " and stay on"}.
          Every point is written to <code>sweeps.csv</code>.
        </p>
      </Panel>

      <Panel
        title="Curve"
        subtitle={`${sweep.rows.A.length + sweep.rows.B.length} points acquired`}
        className="plot-panel"
        actions={
          <div className="plot-controls">
            <Segmented
              value={yMetric}
              onChange={setYMetric}
              options={[
                { value: "measured", label: measuredIsCurrent ? "I" : "V" },
                { value: "ohms", label: "R" },
              ]}
            />
            <Toggle checked={logY} onChange={setLogY} label="log" />
            <span className="divider" />
            <button
              type="button"
              className="btn ghost"
              disabled={!sweep.runId}
              onClick={exportRun}
            >
              Export run CSV
            </button>
            <button
              type="button"
              className="btn subtle"
              disabled={!sweep.runId}
              onClick={() => actions.download("sweeps", "sweeps.csv")}
            >
              All sweeps
            </button>
            <button type="button" className="btn subtle" onClick={actions.clearSweep}>
              Clear
            </button>
          </div>
        }
      >
        <Plot
          series={series}
          xLabel={activeSource === "VOLT" ? "Voltage (V)" : "Current (A)"}
          yLabel={`${yMetric === "ohms" ? "Resistance" : measuredIsCurrent ? "Current" : "Voltage"} (${yUnit})`}
          logY={logY}
          height={360}
          empty={connected ? "configure a sweep and press Run" : "no instrument connected"}
          xFormat={(value) => si(value, activeSource === "VOLT" ? "V" : "A", 3)}
          yFormat={(value) => {
            const parts = siParts(value, yUnit, 3);
            return `${parts.number}${parts.prefix}`;
          }}
        />

        {devices.length ? (
          <div className="sweep-table">
            <div className="sweep-table-head">
              <span>unit</span>
              <span>points</span>
              <span>final {activeSource === "VOLT" ? "V" : "I"}</span>
              <span>final {yMetric === "ohms" ? "R" : measuredIsCurrent ? "I" : "V"}</span>
              <span>min</span>
              <span>max</span>
            </div>
            {devices.map((letter) => {
              const rows = sweep.rows[letter];
              const values = rows
                .map((row) => (yMetric === "ohms" ? row.ohms : measuredIsCurrent ? row.amps : row.volts))
                .filter((value): value is number => value !== null && Number.isFinite(value));
              const last = rows[rows.length - 1];
              return (
                <div className="sweep-table-row" key={letter}>
                  <span className={`unit-tag dev-${letter.toLowerCase()}`}>{letter}</span>
                  <span>{rows.length}</span>
                  <span>{si(last?.x, activeSource === "VOLT" ? "V" : "A")}</span>
                  <span>
                    {si(
                      yMetric === "ohms" ? last?.ohms : measuredIsCurrent ? last?.amps : last?.volts,
                      yUnit,
                    )}
                  </span>
                  <span>{si(values.length ? Math.min(...values) : null, yUnit)}</span>
                  <span>{si(values.length ? Math.max(...values) : null, yUnit)}</span>
                </div>
              );
            })}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

