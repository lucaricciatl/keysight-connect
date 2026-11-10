import type { AppState } from "../lib/types";
import { shortIdn } from "../lib/format";

export default function Header({
  state,
  wsUp,
  busy,
  onPanic,
}: {
  state: AppState;
  wsUp: boolean;
  busy: string | null;
  onPanic: () => void;
}) {
  const anyOutput = state.devices.A.output || state.devices.B.output;
  return (
    <header className="app-header">
      <div className="brand">
        <div className="brand-mark">
          <span className="brand-pulse" />
        </div>
        <div>
          <h1>Keysight Connect</h1>
          <p>B2901B precision SMU · dual-channel bench control</p>
        </div>
      </div>

      <div className="header-devices">
        {(["A", "B"] as const).map((letter) => {
          const device = state.devices[letter];
          const color = letter === "A" ? "var(--dev-a)" : "var(--dev-b)";
          return (
            <div
              key={letter}
              className={`header-chip${device.connected ? " is-live" : ""}`}
              style={{ "--chip": color } as React.CSSProperties}
            >
              <span className="chip-letter">{letter}</span>
              <span className="chip-text">
                <strong>{device.connected ? shortIdn(device.idn) : "not connected"}</strong>
                <em>
                  {device.connected ? device.address : "—"}
                  {device.connected ? ` · ${device.wire === "4W" ? "4-wire" : "2-wire"}` : ""}
                  {device.output ? " · OUTPUT ON" : ""}
                </em>
              </span>
              <span className={`chip-dot${device.connected ? " on" : ""}`} />
            </div>
          );
        })}
      </div>

      <div className="header-right">
        <span className={`ws-pill${wsUp ? " on" : ""}`}>
          <span className="chip-dot" />
          {wsUp ? "live" : "reconnecting"}
        </span>
        <span className="session-pill">session {state.session?.id ?? "—"}</span>
        {busy ? <span className="busy-pill">{busy}…</span> : null}
        <button
          type="button"
          className={`panic-button${anyOutput ? " is-hot" : ""}`}
          onClick={onPanic}
          title="Switch every output off immediately"
        >
          All outputs off
        </button>
      </div>
    </header>
  );
}
