import { useEffect, useMemo, useRef, useState } from "react";
import { Panel, Toggle } from "./ui";
import { clockTime } from "../lib/format";
import type { AppApi } from "../hooks/useApp";
import type { DeviceLetter } from "../lib/types";

const QUICK = ["*IDN?", ":OUTP?", ":MEAS:CURR?", ":MEAS:VOLT?", ":SYST:ERR?", "*RST"];

export default function ConsoleTab({ app }: { app: AppApi }) {
  const { state, logs, actions } = app;
  const [filter, setFilter] = useState<"all" | DeviceLetter>("all");
  const [target, setTarget] = useState<"A" | "B" | "both">("A");
  const [command, setCommand] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const [response, setResponse] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const listRef = useRef<HTMLDivElement | null>(null);

  const visible = useMemo(
    () =>
      logs.filter((entry) => filter === "all" || entry.device === filter || entry.device === null),
    [logs, filter],
  );

  useEffect(() => {
    if (!autoScroll) return;
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [visible, autoScroll]);

  if (!state) return null;

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setHistory((prev) => [...prev.slice(-49), trimmed]);
    setHistoryIndex(null);
    setCommand("");
    setErrors([]);
    const targets: DeviceLetter[] =
      target === "both" ? (["A", "B"] as const).filter((l) => state.devices[l].connected) : [target];
    if (!targets.length) {
      setResponse(null);
      setErrors(["No instrument connected"]);
      return;
    }
    const answers: string[] = [];
    const problems: string[] = [];
    for (const letter of targets) {
      if (!state.devices[letter].connected) continue;
      try {
        const result = (await actions.scpi(letter, trimmed)) as {
          response?: string;
          errors?: string[];
        };
        if (result.response) answers.push(`${letter}: ${result.response}`);
        if (result.errors?.length) problems.push(`${letter}: ${result.errors.join(" | ")}`);
      } catch (error) {
        problems.push(`${letter}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    setResponse(answers.join("\n") || null);
    setErrors(problems);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      send(command);
      return;
    }
    if (event.key === "ArrowUp" && history.length) {
      event.preventDefault();
      const next = historyIndex === null ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(next);
      setCommand(history[next]);
    }
    if (event.key === "ArrowDown" && historyIndex !== null) {
      event.preventDefault();
      const next = historyIndex + 1;
      if (next >= history.length) {
        setHistoryIndex(null);
        setCommand("");
      } else {
        setHistoryIndex(next);
        setCommand(history[next]);
      }
    }
  };

  return (
    <div className="tab-body console-tab">
      <Panel
        title="SCPI traffic"
        subtitle="Every command the app and the console send"
        className="console-panel"
        actions={
          <div className="plot-controls">
            <div className="segmented small">
              {(["all", "A", "B"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  className={filter === option ? "active" : ""}
                  onClick={() => setFilter(option)}
                >
                  {option === "all" ? "All" : `Unit ${option}`}
                </button>
              ))}
            </div>
            <Toggle checked={autoScroll} onChange={setAutoScroll} label="autoscroll" />
            <button type="button" className="btn subtle" onClick={actions.clearLogs}>
              Clear view
            </button>
          </div>
        }
      >
        <div className="console-list" ref={listRef}>
          {visible.length === 0 ? (
            <p className="console-empty">no traffic yet</p>
          ) : (
            visible.map((entry, index) => (
              <div className={`console-line level-${entry.level}`} key={`${entry.time}-${index}`}>
                <span className="console-time">{clockTime(entry.time)}</span>
                <span className="console-device">{entry.device ?? "·"}</span>
                <span className="console-dir">{entry.level === "tx" ? "»" : entry.level === "rx" ? "«" : "i"}</span>
                <span className="console-text">{entry.text}</span>
              </div>
            ))
          )}
        </div>

        <div className="console-input-row">
          <div className="segmented small">
            {(["A", "B", "both"] as const).map((option) => (
              <button
                key={option}
                type="button"
                className={target === option ? "active" : ""}
                onClick={() => setTarget(option)}
              >
                {option === "both" ? "A+B" : option}
              </button>
            ))}
          </div>
          <input
            className="console-input"
            placeholder=":SOUR:VOLT 1.5   (Enter to send, ↑ for history)"
            value={command}
            spellCheck={false}
            onChange={(event) => setCommand(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <button type="button" className="btn primary" onClick={() => send(command)}>
            Send
          </button>
        </div>

        <div className="quick-row">
          {QUICK.map((quick) => (
            <button key={quick} type="button" className="chip-button" onClick={() => send(quick)}>
              {quick}
            </button>
          ))}
        </div>

        {response ? <pre className="console-response">{response}</pre> : null}
        {errors.length ? (
          <pre className="console-response is-error">{errors.join("\n")}</pre>
        ) : null}
      </Panel>
    </div>
  );
}
