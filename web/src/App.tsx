import { useState } from "react";
import Header from "./components/Header";
import ConnectionPanel from "./components/ConnectionPanel";
import MeasureTab from "./components/MeasureTab";
import SweepTab from "./components/SweepTab";
import PulseTab from "./components/PulseTab";
import ConsoleTab from "./components/ConsoleTab";
import DataTab from "./components/DataTab";
import { useApp } from "./hooks/useApp";

type TabKey = "measure" | "sweep" | "pulse" | "console" | "data";

export default function App() {
  const app = useApp();
  const [tab, setTab] = useState<TabKey>("measure");

  if (!app.state) {
    return (
      <div className="boot">
        <div className="boot-mark" />
        <h1>Keysight Connect</h1>
        <p>{app.error ? `Backend not reachable — ${app.error}` : "Connecting to the instrument server…"}</p>
        <code>python3 -m server.app --port 8000</code>
      </div>
    );
  }

  const { state, busy, actions } = app;
  const anyOutput = state.devices.A.output || state.devices.B.output;

  const tabs: { key: TabKey; label: string; badge?: string }[] = [
    { key: "measure", label: "Source / Measure" },
    { key: "sweep", label: "I–V Sweep", badge: app.sweep.running ? "live" : undefined },
    { key: "pulse", label: "Pulses", badge: app.pulse.running ? "live" : undefined },
    { key: "console", label: "SCPI Console" },
    { key: "data", label: "Data" },
  ];

  return (
    <div className={`app${anyOutput ? " outputs-live" : ""}`}>
      <Header state={state} wsUp={app.wsUp} busy={busy} onPanic={() => actions.panic()} />

      <div className="layout">
        <aside className="sidebar">
          <ConnectionPanel app={app} />
        </aside>

        <main className="main">
          <nav className="tabs" role="tablist">
            {tabs.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={tab === item.key}
                className={`tab${tab === item.key ? " active" : ""}`}
                onClick={() => setTab(item.key)}
              >
                {item.label}
                {item.badge ? <span className="tab-badge">{item.badge}</span> : null}
              </button>
            ))}
            <div className="tab-spacer" />
            <span className="tab-meta">
              {state.devices.A.connected || state.devices.B.connected
                ? `${[state.devices.A, state.devices.B].filter((d) => d.connected).length} unit(s) online`
                : "no hardware"}
            </span>
          </nav>

          <div className="tab-content">
            {tab === "measure" ? <MeasureTab app={app} /> : null}
            {tab === "sweep" ? <SweepTab app={app} /> : null}
            {tab === "pulse" ? <PulseTab app={app} /> : null}
            {tab === "console" ? <ConsoleTab app={app} /> : null}
            {tab === "data" ? <DataTab app={app} /> : null}
          </div>
        </main>
      </div>

      {app.error ? (
        <div className="toast" role="alert">
          <div>
            <strong>Action failed</strong>
            <p>{app.error}</p>
          </div>
          <button type="button" onClick={actions.dismissError} aria-label="Dismiss">
            ✕
          </button>
        </div>
      ) : null}
    </div>
  );
}
