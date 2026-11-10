import { useEffect, useMemo, useState } from "react";
import NumberField from "./NumberField";
import Plot from "./Plot";
import Readout, { StatRow } from "./Readout";
import { Panel, Segmented, Toggle } from "./ui";
import {
  quantityLabel,
  quantityUnit,
  si,
  siParts,
  sourceUnit,
} from "../lib/format";
import type { AppApi } from "../hooks/useApp";
import type { DeviceLetter, Quantity, Reading, SourceFunc, WireMode } from "../lib/types";

const DEVICE_COLORS: Record<DeviceLetter, string> = {
  A: "var(--dev-a)",
  B: "var(--dev-b)",
};

const LEVEL_UNITS: Record<SourceFunc, { label: string; scale: number }[]> = {
  VOLT: [
    { label: "V", scale: 1 },
    { label: "mV", scale: 1e-3 },
  ],
  CURR: [
    { label: "A", scale: 1 },
    { label: "mA", scale: 1e-3 },
    { label: "µA", scale: 1e-6 },
  ],
};

const LIMIT_UNITS: Record<SourceFunc, { label: string; scale: number }[]> = {
  VOLT: [
    { label: "A", scale: 1 },
    { label: "mA", scale: 1e-3 },
    { label: "µA", scale: 1e-6 },
  ],
  CURR: [
    { label: "V", scale: 1 },
    { label: "mV", scale: 1e-3 },
  ],
};

function pick(reading: Reading, quantity: Quantity): number {
  const value = reading[quantity];
  return value === null || value === undefined ? Number.NaN : value;
}

export default function MeasureTab({ app }: { app: AppApi }) {
  const { state, buffer, actions, busy } = app;
  const [source, setSource] = useState<SourceFunc>("VOLT");
  const [level, setLevel] = useState(0.5);
  const [compliance, setCompliance] = useState(1e-3);
  const [autoRange, setAutoRange] = useState(true);
  const [wire, setWire] = useState<WireMode>("2W");
  const [intervalMs, setIntervalMs] = useState(500);
  const [quantity, setQuantity] = useState<Quantity>("amps");

  useEffect(() => {
    if (!state) return;
    setSource(state.settings.source);
    setLevel(state.settings.level);
    setCompliance(state.settings.compliance);
    setAutoRange(state.settings.auto_range);
    setWire(state.settings.wire);
    setIntervalMs(state.stream.interval_ms);
  }, [state?.settings, state?.stream.interval_ms]);

  const devices = useMemo(
    () => (["A", "B"] as const).filter((letter) => state?.devices[letter].connected),
    [state?.devices.A.connected, state?.devices.B.connected],
  );

  const series = useMemo(() => {
    const all = [...buffer.A, ...buffer.B];
    if (!all.length) return [];
    const start = Math.min(...all.map((reading) => Date.parse(reading.time)));
    return devices
      .filter((letter) => buffer[letter].length > 0)
      .map((letter) => ({
        name: `Device ${letter}`,
        color: letter === "A" ? "#38bdf8" : "#f59e0b",
        points: buffer[letter]
          .slice(-1500)
          .map((reading) => ({
            x: (Date.parse(reading.time) - start) / 1000,
            y: pick(reading, quantity),
          })),
      }));
  }, [buffer, quantity, devices]);

  if (!state) return null;

  const connected = devices.length > 0;
  const anyOutput = devices.some((letter) => state.devices[letter].output);
  const sourceIsVoltage = source === "VOLT";

  const apply = () =>
    actions.configure({
      source,
      level,
      compliance,
      auto_range: autoRange,
      wire,
      sync_mode: state.settings.sync_mode,
    });

  return (
    <div className="tab-body measure-tab">
      <div className="measure-top">
        <Panel
          title="Source"
          subtitle="Applies to every connected unit"
          className="source-card"
          actions={
            <button type="button" className="btn primary" onClick={apply} disabled={busy === "configure"}>
              Apply
            </button>
          }
        >
          <Segmented
            value={source}
            onChange={(next) => {
              setSource(next);
              if (next !== source) {
                setLevel(next === "VOLT" ? 0.5 : 1e-3);
                setCompliance(next === "VOLT" ? 1e-3 : 1);
              }
            }}
            options={[
              { value: "VOLT", label: "Source V" },
              { value: "CURR", label: "Source I" },
            ]}
          />

          <div className="source-fields">
            <NumberField
              label={sourceIsVoltage ? "Voltage level" : "Current level"}
              value={level}
              onChange={setLevel}
              units={LEVEL_UNITS[source]}
            />
            <NumberField
              label="Compliance"
              value={compliance}
              onChange={setCompliance}
              units={LIMIT_UNITS[source]}
              hint={sourceIsVoltage ? "current limit" : "voltage limit"}
            />
          </div>

          <div className="toggle-row">
            <Toggle checked={autoRange} onChange={setAutoRange} label="Auto range" />
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
          <p className="hint-text">
            {wire === "4W"
              ? "Remote (Kelvin) sensing: the sense leads measure at the DUT, so lead resistance is cancelled (not available on the 3 A range)."
              : "Local sensing: voltage is measured at the force terminals, so lead resistance adds to the reading."}
          </p>

          <div className="output-block">
            <button
              type="button"
              className={`output-button${anyOutput ? " is-on" : ""}`}
              disabled={!connected}
              onClick={() => actions.setAllOutputs(!anyOutput)}
            >
              <span className="output-led" />
              {anyOutput ? "Output on" : "Output off"}
            </button>
            <p className="hint-text">
              {connected
                ? `Sourcing ${si(level, sourceUnit(source))} with a ${si(
                    compliance,
                    sourceIsVoltage ? "A" : "V",
                  )} limit`
                : "Connect an instrument to enable the output"}
            </p>
          </div>
        </Panel>

        <div className="readout-area">
          {devices.length === 0 ? (
            <div className="empty-state">
              <h3>No instrument connected</h3>
              <p>Point the sidebar at a B2901B on your LAN, or start the mock:</p>
              <code>python3 mock_b2901b.py --port 5025</code>
            </div>
          ) : (
            devices.map((letter) => {
              const device = state.devices[letter];
              const metrics = device.stats[quantity];
              return (
                <div
                  className="device-readouts"
                  key={letter}
                  style={{ "--dev": DEVICE_COLORS[letter] } as never}
                >
                  <div className="device-readouts-head">
                    <span className="device-letter">{letter}</span>
                    <span className="device-readouts-title">
                      {device.idn.split(",")[1] ?? "B2901B"} · {device.address}
                    </span>
                    <button
                      type="button"
                      className={`mini-switch${device.output ? " on" : ""}`}
                      onClick={() => actions.setOutput(letter, !device.output)}
                    >
                      OUT {device.output ? "ON" : "OFF"}
                    </button>
                  </div>
                  <div className="readout-grid">
                    <Readout label="Voltage" value={device.last?.volts} unit="V" accent="#e8eef6" />
                    <Readout
                      label="Current"
                      value={device.last?.amps}
                      unit="A"
                      accent={DEVICE_COLORS[letter]}
                    />
                    <Readout label="Resistance" value={device.last?.ohms} unit="Ω" accent="#c4b5fd" />
                    <Readout label="Power" value={device.last?.watts} unit="W" accent="#5eead4" />
                  </div>
                  <StatRow
                    min={metrics.min}
                    max={metrics.max}
                    mean={metrics.mean}
                    std={metrics.std}
                    count={metrics.count}
                    unit={quantityUnit(quantity)}
                  />
                </div>
              );
            })
          )}
        </div>
      </div>

      <Panel
        title="Live measurement"
        subtitle="Every sample is appended to the session CSV"
        className="plot-panel"
        actions={
          <div className="plot-controls">
            <Segmented
              value={quantity}
              onChange={setQuantity}
              options={[
                { value: "volts", label: "V" },
                { value: "amps", label: "I" },
                { value: "ohms", label: "R" },
                { value: "watts", label: "P" },
              ]}
            />
            <span className="divider" />
            <button
              type="button"
              className="btn ghost"
              disabled={!connected || busy === "measure"}
              onClick={() => actions.measureOnce()}
            >
              Measure once
            </button>
            <button
              type="button"
              className={`btn ${state.stream.running ? "danger" : "primary"}`}
              disabled={!connected}
              onClick={() =>
                state.stream.running ? actions.streamStop() : actions.streamStart(intervalMs)
              }
            >
              {state.stream.running ? "Stop" : "Run continuous"}
            </button>
            <div className="interval-field">
              <NumberField
                value={intervalMs}
                onChange={(value) => setIntervalMs(Math.round(value))}
                units={[
                  { label: "ms", scale: 1 },
                  { label: "s", scale: 1000 },
                ]}
              />
            </div>
            <button type="button" className="btn subtle" onClick={actions.clearBuffer}>
              Clear
            </button>
          </div>
        }
      >
        <Plot
          series={series}
          xLabel="Elapsed time (s)"
          yLabel={`${quantityLabel(quantity)} (${quantityUnit(quantity)})`}
          logY={quantity === "ohms"}
          height={330}
          empty={connected ? "press Run continuous to start plotting" : "no instrument connected"}
          yFormat={(value) => {
            const parts = siParts(value, quantityUnit(quantity), 3);
            return `${parts.number}${parts.prefix}`;
          }}
        />
        <div className="strip-footer">
          <span>
            <em>interval</em> {state.stream.interval_ms} ms
          </span>
          <span>
            <em>samples</em> {state.devices.A.stats.n + state.devices.B.stats.n}
          </span>
          <span>
            <em>buffered</em> {buffer.A.length + buffer.B.length}
          </span>
          <span>
            <em>mode</em> {state.settings.sync_mode}
          </span>
        </div>
      </Panel>
    </div>
  );
}
