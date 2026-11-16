import { useCallback, useEffect, useRef, useState } from "react";
import { api, get } from "../lib/api";
import type {
  AppState,
  ConnectionConfig,
  DeviceLetter,
  LogEntry,
  PulseState,
  Reading,
  SourceFunc,
  SweepState,
  SyncMode,
  WireMode,
} from "../lib/types";

const MAX_BUFFER = 4000;
const MAX_LOGS = 1500;

const emptySweepRows = () => ({ A: [], B: [] }) as SweepState["rows"];
const emptyPulseRows = () => ({ A: [], B: [] }) as PulseState["rows"];

export const initialSweep: SweepState = {
  runId: null,
  source: "VOLT",
  running: false,
  aborted: false,
  rows: emptySweepRows(),
  params: null,
};

export const initialPulse: PulseState = {
  runId: null,
  source: "VOLT",
  running: false,
  aborted: false,
  rows: emptyPulseRows(),
  stats: { A: null, B: null },
  skew_ms: null,
  params: null,
};

type SocketMessage = Record<string, any> & { type: string };

export function useApp() {
  const [state, setState] = useState<AppState | null>(null);
  const [buffer, setBuffer] = useState<Record<DeviceLetter, Reading[]>>({
    A: [],
    B: [],
  });
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [sweep, setSweep] = useState<SweepState>(initialSweep);
  const [pulse, setPulse] = useState<PulseState>(initialPulse);
  const [wsUp, setWsUp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const mounted = useRef(true);

  const refreshLog = useCallback(async () => {
    try {
      const data = await api.log(300);
      setLogs(data.entries as LogEntry[]);
    } catch {
      /* the websocket will fill it in */
    }
  }, []);

  // Rebuild the sweep/pulse plots from the server's cached last run, so a
  // missed websocket `*_done` frame (drop, reconnect, background tab) does not
  // leave the curve permanently empty.
  const restoreRuns = useCallback((next: AppState) => {
    if (next.job?.state === "running") return;   // never clobber a live run
    const ls = next.last_sweep;
    if (ls) {
      setSweep({
        runId: ls.run_id,
        source: ls.source,
        running: false,
        aborted: Boolean(ls.aborted),
        rows: { A: ls.series.A ?? [], B: ls.series.B ?? [] },
        params: {
          start: ls.params.start,
          stop: ls.params.stop,
          points: ls.params.points,
          delay_ms: ls.params.delay_ms,
          compliance: ls.params.compliance,
        },
      });
    }
    const lp = next.last_pulse;
    if (lp) {
      setPulse({
        runId: lp.run_id,
        source: lp.source,
        running: false,
        aborted: Boolean(lp.aborted),
        rows: { A: lp.series.A?.rows ?? [], B: lp.series.B?.rows ?? [] },
        stats: { A: lp.series.A?.stats ?? null, B: lp.series.B?.stats ?? null },
        skew_ms: lp.skew_ms ?? null,
        params: {
          peak: lp.params.peak,
          base: lp.params.base,
          width_ms: lp.params.width_ms,
          period_ms: lp.params.period_ms,
          count: lp.params.count,
        },
      });
    }
  }, []);

  const refreshState = useCallback(async () => {
    try {
      const next = (await get("/api/state")) as AppState;
      setState(next);
      restoreRuns(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [restoreRuns]);

  useEffect(() => {
    mounted.current = true;
    refreshState();
    refreshLog();
    return () => {
      mounted.current = false;
    };
  }, [refreshState, refreshLog]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;

    const handle = (msg: SocketMessage) => {
      switch (msg.type) {
        case "hello":
          if (msg.state) setState(msg.state);
          break;
        case "device":
          if (msg.device) {
            const device = msg.device;
            setState((prev) =>
              prev
                ? {
                    ...prev,
                    devices: { ...prev.devices, [device.letter]: device },
                  }
                : prev,
            );
          }
          break;
        case "settings":
          setState((prev) =>
            prev && msg.settings ? { ...prev, settings: msg.settings } : prev,
          );
          break;
        case "stream":
          setState((prev) =>
            prev && msg.stream ? { ...prev, stream: msg.stream } : prev,
          );
          break;
        case "job":
          setState((prev) => (prev && msg.job ? { ...prev, job: msg.job } : prev));
          break;
        case "session":
          setState((prev) =>
            prev && msg.session ? { ...prev, session: msg.session } : prev,
          );
          break;
        case "reading": {
          if (!msg.readings) break;
          setBuffer((prev) => {
            const next = { ...prev };
            for (const reading of msg.readings ?? []) {
              const letter = reading.device as DeviceLetter;
              const list = [...(next[letter] ?? []), reading];
              next[letter] = list.slice(-MAX_BUFFER);
            }
            return next;
          });
          setState((prev) => {
            if (!prev) return prev;
            const devices = { ...prev.devices };
            for (const reading of msg.readings ?? []) {
              const letter = reading.device as DeviceLetter;
              devices[letter] = {
                ...devices[letter],
                last: reading,
                stats: reading.stats ?? devices[letter].stats,
              };
            }
            return { ...prev, devices };
          });
          break;
        }
        case "sweep_start":
          setSweep({
            runId: msg.run_id ?? null,
            source: msg.source ?? "VOLT",
            running: true,
            aborted: false,
            rows: { A: [], B: [] },
            params: {
              start: msg.params?.start ?? 0,
              stop: msg.params?.stop ?? 1,
              points: msg.params?.points ?? 0,
              delay_ms: msg.params?.delay_ms ?? 0,
              compliance: msg.params?.compliance ?? 0,
            },
          });
          break;
        case "sweep_point":
          if (msg.rows?.[0]) {
            setSweep((prev) => {
              const rows = { ...prev.rows };
              for (const row of msg.rows ?? []) {
                const letter = row.device as DeviceLetter;
                rows[letter] = [...(rows[letter] ?? []), row];
              }
              return { ...prev, rows, running: true };
            });
          }
          break;
        case "sweep_done":
          setSweep((prev) => ({
            ...prev,
            running: false,
            aborted: Boolean(msg.aborted),
            rows: msg.series
              ? { A: msg.series.A ?? [], B: msg.series.B ?? [] }
              : prev.rows,
          }));
          break;
        case "pulse_start":
          setPulse({
            runId: msg.run_id ?? null,
            source: msg.source ?? "VOLT",
            running: true,
            aborted: false,
            rows: { A: [], B: [] },
            stats: { A: null, B: null },
            skew_ms: msg.skew_ms ?? null,
            params: {
              peak: msg.params?.peak ?? 0,
              base: msg.params?.base ?? 0,
              width_ms: msg.params?.width_ms ?? 1,
              period_ms: msg.params?.period_ms ?? 10,
              count: msg.params?.count ?? 0,
            },
          });
          break;
        case "pulse_point":
          if (msg.device) {
            const letter = msg.device as DeviceLetter;
            setPulse((prev) => ({
              ...prev,
              running: true,
              rows: {
                ...prev.rows,
                [letter]: [
                  ...(prev.rows[letter] ?? []),
                  {
                    index: msg.index ?? 0,
                    measured: msg.measured ?? 0,
                    volts: msg.volts ?? 0,
                    amps: msg.amps ?? 0,
                    ohms: msg.ohms ?? null,
                  },
                ],
              },
            }));
          }
          break;
        case "pulse_done": {
          const series = msg.series ?? {};
          setPulse((prev) => ({
            ...prev,
            running: false,
            aborted: Boolean(msg.aborted),
            skew_ms: msg.skew_ms ?? prev.skew_ms,
            rows: {
              A: series.A?.rows ?? prev.rows.A,
              B: series.B?.rows ?? prev.rows.B,
            },
            stats: {
              A: series.A?.stats ?? prev.stats.A,
              B: series.B?.stats ?? prev.stats.B,
            },
          }));
          break;
        }
        case "log":
          if (msg.entry) {
            const entry = msg.entry;
            setLogs((prev) => [...prev.slice(-MAX_LOGS), entry]);
          }
          break;
        default:
          break;
      }
    };

    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${window.location.host}/ws`);
      socket.onopen = () => {
        setWsUp(true);
        refreshState();
      };
      socket.onmessage = (event) => {
        try {
          handle(JSON.parse(event.data) as SocketMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        setWsUp(false);
        if (!closed) retry = window.setTimeout(connect, 1500);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      socket?.close();
    };
  }, [refreshState]);

  const act = useCallback(
    async <T,>(label: string, fn: () => Promise<T>): Promise<T | undefined> => {
      setBusy(label);
      setError(null);
      try {
        const result = await fn();
        await refreshState();
        return result;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return undefined;
      } finally {
        if (mounted.current) setBusy(null);
      }
    },
    [refreshState],
  );

  const actions = {
    refreshState,
    connect: (letter: DeviceLetter, cfg: ConnectionConfig) =>
      act(`connect ${letter}`, () => api.connect(letter, cfg)),
    disconnect: (letter: DeviceLetter) =>
      act(`disconnect ${letter}`, () => api.disconnect(letter)),
    setOutput: (letter: DeviceLetter, on: boolean) =>
      act("output", () => api.output(letter, on)),
    setAllOutputs: (on: boolean) => act("outputs", () => api.outputs(on)),
    panic: () => act("panic", () => api.panic()),
    configure: (payload: {
      source: SourceFunc;
      level: number;
      compliance: number;
      auto_range: boolean;
      wire: WireMode;
      sync_mode?: SyncMode;
    }) => act("configure", () => api.configure(payload)),
    measureOnce: () =>
      act("measure", async () => {
        const res = (await api.measure()) as {
          readings?: (Reading & { device?: DeviceLetter })[];
        };
        // Feed the one-shot into the live buffer too, so the plot shows it even
        // if the matching websocket `reading` frame was missed.
        const readings = res?.readings;
        if (Array.isArray(readings)) {
          setBuffer((prev) => {
            const next = { ...prev };
            for (const r of readings) {
              const letter = r.device;
              if (!letter) continue;
              const list = next[letter] ?? [];
              const tail = list[list.length - 1];
              if (tail && tail.time === r.time) continue; // already via ws
              next[letter] = [...list, r].slice(-MAX_BUFFER);
            }
            return next;
          });
        }
        return res;
      }),
    streamStart: (interval_ms: number) =>
      act("stream", () => api.streamStart(interval_ms)),
    streamStop: () => act("stream", () => api.streamStop()),
    runSweep: (payload: Record<string, unknown>) =>
      act("sweep", () => api.sweep(payload)),
    runPulse: (payload: Record<string, unknown>) =>
      act("pulse", () => api.pulse(payload)),
    stopJob: () => act("stop", () => api.stopJob()),
    scpi: (letter: DeviceLetter, command: string) =>
      api.scpi(letter, command),
    scan: (ip: string, port: number) =>
      act("scan", () => api.scan(ip, port)),
    newSession: (name: string) =>
      act("session", async () => {
        await api.newSession(name);
        setBuffer({ A: [], B: [] });
      }),
    download: (kind: string, fallback: string) =>
      act("download", () => api.download(kind, fallback)),
    clearBuffer: () => setBuffer({ A: [], B: [] }),
    clearSweep: () => setSweep(initialSweep),
    clearPulse: () => setPulse(initialPulse),
    clearLogs: () => setLogs([]),
    dismissError: () => setError(null),
  };

  return {
    state,
    buffer,
    logs,
    sweep,
    pulse,
    wsUp,
    busy,
    error,
    actions,
  };
}

export type AppApi = ReturnType<typeof useApp>;
