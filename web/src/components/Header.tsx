import type { AppState } from "../lib/types";
import { shortIdn } from "../lib/format";
import { useThemeToggle } from "../lib/theme";

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
  const [theme, toggleTheme] = useThemeToggle();
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
          className="theme-toggle"
          onClick={toggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
            </svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          )}
        </button>
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
