import { useEffect, useState } from "react";
import { Panel, Segmented } from "./ui";
import { si, shortIdn } from "../lib/format";
import type { ConnectionConfig, DeviceLetter, SyncMode } from "../lib/types";
import type { AppApi } from "../hooks/useApp";

const DEFAULTS: Record<DeviceLetter, ConnectionConfig> = {
  A: { address: "127.0.0.1", transport: "socket", port: 5025, timeout_ms: 10000 },
  B: { address: "127.0.0.1", transport: "socket", port: 5026, timeout_ms: 10000 },
};

const STORAGE_KEY = "keysight-connect.connection";

function loadPersisted(): Record<DeviceLetter, ConnectionConfig> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<Record<DeviceLetter, Partial<ConnectionConfig>>>;
    const merged: Record<DeviceLetter, ConnectionConfig> = { ...DEFAULTS };
    for (const letter of ["A", "B"] as const) {
      const saved = parsed[letter];
      if (!saved) continue;
      merged[letter] = {
        address: typeof saved.address === "string" ? saved.address : DEFAULTS[letter].address,
        transport: typeof saved.transport === "string" ? saved.transport : DEFAULTS[letter].transport,
        port: typeof saved.port === "number" ? saved.port : DEFAULTS[letter].port,
        timeout_ms:
          typeof saved.timeout_ms === "number" ? saved.timeout_ms : DEFAULTS[letter].timeout_ms,
      };
    }
    return merged;
  } catch {
    return DEFAULTS;
  }
}

export default function ConnectionPanel({ app }: { app: AppApi }) {
  const { state, actions, busy } = app;
  const [forms, setForms] = useState<Record<DeviceLetter, ConnectionConfig>>(loadPersisted);
  const [deviceB, setDeviceB] = useState(false);
  const [scanResult, setScanResult] = useState<string | null>(null);
  const [syncMode, setSyncMode] = useState<SyncMode>("trigger");

  // Persist the address/port/transport/timeout so a restart remembers the units.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(forms));
    } catch {
      /* storage may be unavailable (private mode); ignore */
    }
  }, [forms]);

  useEffect(() => {
    if (!state) return;
    setSyncMode(state.settings.sync_mode);
    setForms((prev) => {
      const next = { ...prev };
      (["A", "B"] as const).forEach((letter) => {
        const device = state.devices[letter];
        if (device.connected) {
          next[letter] = {
            address: device.address,
            transport: device.transport,
            port: device.port,
            timeout_ms: device.timeout_ms,
          };
        }
      });
      return next;
    });
    if (state.devices.B.connected) setDeviceB(true);
  }, [state]);

  if (!state) return null;

  const update = (letter: DeviceLetter, patch: Partial<ConnectionConfig>) =>
    setForms((prev) => ({ ...prev, [letter]: { ...prev[letter], ...patch } }));

  const changeSync = (mode: SyncMode) => {
    setSyncMode(mode);
    const anyConnected = state.devices.A.connected || state.devices.B.connected;
    if (anyConnected) {
      actions.configure({ ...state.settings, sync_mode: mode });
    }
  };

  const scan = async () => {
    setScanResult("scanning…");
    try {
      const found = (await actions.scan(
        forms[deviceB ? "B" : "A"].address,
        forms[deviceB ? "B" : "A"].port,
      )) as { found?: { ip: string; idn: string }[] } | undefined;
      const hits = found?.found ?? [];
      setScanResult(
        hits.length
          ? `${hits.length} found: ${hits.map((h) => h.ip).join(", ")}`
          : "no instruments answered",
      );
    } catch {
      setScanResult("scan failed");
    }
  };

  const renderDevice = (letter: DeviceLetter) => {
    const device = state.devices[letter];
    const form = forms[letter];
    const color = letter === "A" ? "var(--dev-a)" : "var(--dev-b)";
    return (
      <div className="device-block" style={{ "--dev": color } as React.CSSProperties}>
        <div className="device-block-head">
          <span className="device-letter">{letter}</span>
          <div className="device-id">
            <strong>{device.connected ? shortIdn(device.idn) : "idle"}</strong>
            <em>{device.connected ? device.address : "configure and connect"}</em>
          </div>
          <span className={`chip-dot${device.connected ? " on" : ""}`} />
        </div>

        <div className="form-grid">
          <label className="field">
            <span className="field-label">Address</span>
            <div className="input-wrap">
              <input
                value={form.address}
                spellCheck={false}
                onChange={(event) => update(letter, { address: event.target.value })}
              />
            </div>
          </label>
          <label className="field">
            <span className="field-label">Port</span>
            <div className="input-wrap">
              <input
                value={form.port}
                inputMode="numeric"
                onChange={(event) =>
                  update(letter, { port: Number(event.target.value) || 5025 })
                }
              />
            </div>
          </label>
          <label className="field">
            <span className="field-label">Transport</span>
            <div className="input-wrap">
              <select
                value={form.transport}
                onChange={(event) => update(letter, { transport: event.target.value })}
              >
                <option value="socket">socket 5025</option>
                <option value="vxi11">VXI-11</option>
              </select>
            </div>
          </label>
          <label className="field">
            <span className="field-label">Timeout</span>
            <div className="input-wrap">
              <input
                value={form.timeout_ms}
                inputMode="numeric"
                onChange={(event) =>
                  update(letter, { timeout_ms: Number(event.target.value) || 10000 })
                }
              />
            </div>
          </label>
        </div>

        <div className="device-actions">
          {device.connected ? (
            <button
              type="button"
              className="btn ghost"
              disabled={busy === `disconnect ${letter}`}
              onClick={() => actions.disconnect(letter)}
            >
              Disconnect
            </button>
          ) : (
            <button
              type="button"
              className="btn primary"
              disabled={busy === `connect ${letter}`}
              onClick={() => actions.connect(letter, form)}
            >
              Connect
            </button>
          )}
          <button type="button" className="btn subtle" onClick={scan}>
            Scan LAN
          </button>
          {device.connected ? (
            <span className="device-output">
              output
              <button
                type="button"
                className={`mini-switch${device.output ? " on" : ""}`}
                onClick={() => actions.setOutput(letter, !device.output)}
              >
                {device.output ? "ON" : "OFF"}
              </button>
            </span>
          ) : null}
        </div>
        {scanResult ? <p className="field-hint scan-result">{scanResult}</p> : null}
      </div>
    );
  };

  return (
    <>
      <Panel
        title="Instruments"
        subtitle="Raw SCPI over LAN — no VISA runtime needed"
        className="side-panel"
      >
        {renderDevice("A")}
        <label className="device-toggle">
          <input
            type="checkbox"
            checked={deviceB}
            onChange={(event) => {
              setDeviceB(event.target.checked);
              if (!event.target.checked && state.devices.B.connected) {
                actions.disconnect("B");
              }
            }}
          />
          Second SMU (device B)
        </label>
        {deviceB ? renderDevice("B") : null}
      </Panel>

      <Panel title="Synchronisation" className="side-panel">
        <Segmented
          value={syncMode}
          onChange={changeSync}
          options={[
            { value: "trigger", label: "BUS" },
            { value: "parallel", label: "Parallel" },
            { value: "sequential", label: "Seq" },
          ]}
        />
        <p className="hint-text">
          BUS trigger fires both units from one <code>*TRG</code>, the closest to
          simultaneous the LAN link allows. Parallel reads overlap; sequential is
          the most predictable.
        </p>
        <div className="mini-stats">
          <span>
            <em>mode</em> {state.settings.sync_mode}
          </span>
          <span>
            <em>samples</em> {state.devices.A.stats.n + state.devices.B.stats.n}
          </span>
        </div>
      </Panel>

      <Panel title="Sourcing" className="side-panel">
        <div className="mini-stats">
          <span>
            <em>source</em>{" "}
            {state.settings.source === "VOLT" ? "voltage" : "current"}
          </span>
          <span>
            <em>level</em>{" "}
            {si(state.settings.level, state.settings.source === "VOLT" ? "V" : "A")}
          </span>
          <span>
            <em>limit</em> {si(state.settings.compliance, state.settings.source === "VOLT" ? "A" : "V")}
          </span>
          <span>
            <em>4-wire</em> {state.settings.four_wire ? "on" : "off"}
          </span>
        </div>
      </Panel>
    </>
  );
}
