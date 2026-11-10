import { useEffect, useMemo, useState } from "react";
import NumberField from "./NumberField";
import Plot from "./Plot";
import { Panel, Segmented, Toggle } from "./ui";
import { downloadCsv, timestampSlug } from "../lib/csv";
import { measureUnit, si, siParts } from "../lib/format";
import type { AppApi } from "../hooks/useApp";
import type { SourceFunc, WireMode } from "../lib/types";

const MS = [
  { label: "ms", scale: 1 },
  { label: "µs", scale: 1e-3 },
];

function PulsePreview({
  base,
  peak,
  width,
  period,
  delay,
  source,
}: {
  base: number;
  peak: number;
  width: number;
  period: number;
  delay: number;
  source: SourceFunc;
}) {
  const unit = source === "VOLT" ? "V" : "A";
  const span = Math.max(Math.abs(base), Math.abs(peak), 1e-12);
  const x = (t: number) => 8 + Math.min(t / Math.max(period, 1e-9), 1) * 384;
  const y = (v: number) => 54 - (v / span) * 38;
  const on = Math.min(delay + width, period);
  const path = [
    `M ${x(0)} ${y(base)}`,
    `L ${x(Math.min(delay, period))} ${y(base)}`,
    `L ${x(Math.min(delay, period))} ${y(peak)}`,
    `L ${x(on)} ${y(peak)}`,
    `L ${x(on)} ${y(base)}`,
    `L ${x(period)} ${y(base)}`,
  ].join(" ");
  return (
    <div className="pulse-preview">
      <svg viewBox="0 0 400 64" preserveAspectRatio="none" role="img" aria-label="pulse shape">
        <line x1="8" y1={y(base)} x2="392" y2={y(base)} className="preview-base" />
        <line x1="8" y1={y(peak)} x2="392" y2={y(peak)} className="preview-peak" />
        <path d={path} className="preview-trace" />
      </svg>
      <div className="pulse-preview-labels">
        <span>
          <em>base</em> {si(base, unit, 3)}
        </span>
        <span>
          <em>peak</em> {si(peak, unit, 3)}
        </span>
        <span>
          <em>width</em> {si(width, "ms", 3)}
        </span>
        <span>
          <em>period</em> {si(period, "ms", 3)}
        </span>
      </div>
    </div>
  );
}

export default function PulseTab({ app }: { app: AppApi }) {
  const { state, pulse, actions, busy } = app;
  const [source, setSource] = useState<SourceFunc>("VOLT");
  const [base, setBase] = useState(0);
  const [peak, setPeak] = useState(0.5);
  const [width, setWidth] = useState(1);
  const [period, setPeriod] = useState(10);
  const [delay, setDelay] = useState(0.5);
  const [aperture, setAperture] = useState(0.1);
  const [compliance, setCompliance] = useState(1e-3);
  const [count, setCount] = useState(100);
  const [outputOff, setOutputOff] = useState(true);
  const [wire, setWire] = useState<WireMode>("2W");

  useEffect(() => {
    if (state?.settings.wire) setWire(state.settings.wire);
  }, [state?.settings.wire]);

  const devices = useMemo(
    () => (["A", "B"] as const).filter((letter) => pulse.rows[letter].length > 0),
    [pulse.rows],
  );

  const series = useMemo(
    () =>
      devices.map((letter) => ({
        name: `Device ${letter}`,
        color: letter === "A" ? "#38bdf8" : "#f59e0b",
        points: pulse.rows[letter].map((row) => ({ x: row.index, y: row.measured })),
      })),
    [devices, pulse.rows],
  );

  if (!state) return null;

  const connected = state.devices.A.connected || state.devices.B.connected;
  const running = pulse.running && state.job.state === "running";
  const measuredUnit = measureUnit(pulse.runId ? pulse.source : source);
  const activeSource = pulse.runId ? pulse.source : source;
  const timingError =
    aperture > width
      ? "Aperture cannot be wider than the pulse"
      : delay + width >= period
        ? "Delay + width must fit inside the period"
        : null;

  const run = () =>
    actions.runPulse({
      source,
      base,
      peak,
      width_ms: width,
      period_ms: period,
      delay_ms: delay,
      aperture_ms: aperture,
      compliance,
      count,
      output_off: outputOff,
      wire,
    });

  const exportRun = () => {
    if (!pulse.runId) return;
    const rows: (string | number | null)[][] = [
      ["device", "index", "volts", "amps", "ohms", "measured"],
    ];
    (["A", "B"] as const).forEach((letter) => {
      pulse.rows[letter].forEach((row) => {
        rows.push([letter, row.index, row.volts, row.amps, row.ohms, row.measured]);
      });
    });
    downloadCsv(`pulse-${pulse.runId}-${timestampSlug()}.csv`, rows);
  };

  return (
    <div className="tab-body pulse-tab">
      <Panel
        title="Pulse generator"
        subtitle="Hardware-timed train — the instrument clocks it, not the browser"
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
              disabled={!connected || running || Boolean(timingError) || busy === "pulse"}
              onClick={run}
            >
              Run pulse train
            </button>
          </div>
        }
      >
        <div className="segmented">
          <button
            type="button"
            className={source === "VOLT" ? "active" : ""}
            onClick={() => setSource("VOLT")}
          >
            Source V
          </button>
          <button
            type="button"
            className={source === "CURR" ? "active" : ""}
            onClick={() => setSource("CURR")}
          >
            Source I
          </button>
        </div>

        <div className="form-grid two">
          <NumberField
            label="Base"
            value={base}
            onChange={setBase}
            units={
              source === "VOLT"
                ? [
                    { label: "V", scale: 1 },
                    { label: "mV", scale: 1e-3 },
                  ]
                : [
                    { label: "A", scale: 1 },
                    { label: "mA", scale: 1e-3 },
                  ]
            }
          />
          <NumberField
            label="Peak"
            value={peak}
            onChange={setPeak}
            units={
              source === "VOLT"
                ? [
                    { label: "V", scale: 1 },
                    { label: "mV", scale: 1e-3 },
                  ]
                : [
                    { label: "A", scale: 1 },
                    { label: "mA", scale: 1e-3 },
                  ]
            }
          />
          <NumberField label="Width" value={width} onChange={setWidth} units={MS} />
          <NumberField label="Period" value={period} onChange={setPeriod} units={MS} />
          <NumberField label="Delay" value={delay} onChange={setDelay} units={MS} />
          <NumberField label="Aperture" value={aperture} onChange={setAperture} units={MS} />
          <NumberField
            label="Compliance"
            value={compliance}
            onChange={setCompliance}
            units={
              source === "VOLT"
                ? [
                    { label: "A", scale: 1 },
                    { label: "mA", scale: 1e-3 },
                    { label: "µA", scale: 1e-6 },
                  ]
                : [
                    { label: "V", scale: 1 },
                    { label: "mV", scale: 1e-3 },
                  ]
            }
          />
          <NumberField label="Pulses" value={count} onChange={setCount} units={[{ label: "", scale: 1 }]} />
        </div>

        <Toggle checked={outputOff} onChange={setOutputOff} label="Output off when finished" />

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

        <PulsePreview
          base={base}
          peak={peak}
          width={width}
          period={period}
          delay={delay}
          source={source}
        />

        {timingError ? <p className="warn-text">{timingError}</p> : null}

        {pulse.params ? (
          <div className="sweep-summary">
            <span>
              <em>samples</em> {pulse.rows.A.length || pulse.rows.B.length}
            </span>
            <span>
              <em>skew</em> {pulse.skew_ms === null ? "—" : `${pulse.skew_ms.toFixed(3)} ms`}
            </span>
            <span>
              <em>state</em> {running ? "running" : pulse.aborted ? "stopped" : "complete"}
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
      </Panel>

      <Panel
        title="Pulse response"
        subtitle={`${pulse.rows.A.length + pulse.rows.B.length} samples`}
        className="plot-panel"
        actions={
          <div className="plot-controls">
            <button type="button" className="btn ghost" disabled={!pulse.runId} onClick={exportRun}>
              Export run CSV
            </button>
            <button
              type="button"
              className="btn subtle"
              disabled={!pulse.runId}
              onClick={() => actions.download("pulses", "pulses.csv")}
            >
              All pulses
            </button>
            <button type="button" className="btn subtle" onClick={actions.clearPulse}>
              Clear
            </button>
          </div>
        }
      >
        <Plot
          series={series}
          xLabel="Pulse index"
          yLabel={`Measured ${activeSource === "VOLT" ? "current" : "voltage"} (${measuredUnit})`}
          height={300}
          empty={connected ? "configure the train and press Run" : "no instrument connected"}
          xFormat={(value) => String(Math.round(value))}
          yFormat={(value) => {
            const parts = siParts(value, measuredUnit, 3);
            return `${parts.number}${parts.prefix}`;
          }}
        />

        <div className="pulse-stats">
          {(["A", "B"] as const)
            .filter((letter) => pulse.stats[letter])
            .map((letter) => {
              const stats = pulse.stats[letter]!;
              const unit = measuredUnit;
              const resistance =
                stats.mean !== null && stats.mean !== 0
                  ? Math.abs((pulse.params?.peak ?? 0) / stats.mean)
                  : null;
              return (
                <div className="pulse-stat-card" key={letter}>
                  <header>
                    <span className={`unit-tag dev-${letter.toLowerCase()}`}>{letter}</span>
                    <strong>{stats.n} pulses</strong>
                  </header>
                  <div className="kv-grid">
                    <span>
                      <em>mean</em> {si(stats.mean, unit)}
                    </span>
                    <span>
                      <em>min</em> {si(stats.min, unit)}
                    </span>
                    <span>
                      <em>max</em> {si(stats.max, unit)}
                    </span>
                    <span>
                      <em>σ</em> {si(stats.std, unit)}
                    </span>
                    <span>
                      <em>R ≈</em> {si(resistance, "Ω")}
                    </span>
                    <span>
                      <em>duty</em>{" "}
                      {pulse.params
                        ? `${((pulse.params.width_ms / Math.max(pulse.params.period_ms, 1e-9)) * 100).toFixed(1)} %`
                        : "—"}
                    </span>
                  </div>
                </div>
              );
            })}
        </div>
      </Panel>
    </div>
  );
}
