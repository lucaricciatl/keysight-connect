import type { ConnectionConfig, SourceFunc, SyncMode, WireMode } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const get = <T,>(path: string) => request<T>(path);
export const post = <T,>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  state: () => get("/api/state"),
  connect: (letter: string, cfg: ConnectionConfig) =>
    post(`/api/devices/${letter}/connect`, cfg),
  disconnect: (letter: string) => post(`/api/devices/${letter}/disconnect`),
  output: (letter: string, on: boolean) =>
    post(`/api/devices/${letter}/output`, { on }),
  outputs: (on: boolean) => post("/api/outputs", { on }),
  panic: () => post("/api/panic"),
  scpi: (letter: string, command: string) =>
    post<{ response: string; errors: string[] }>(`/api/devices/${letter}/scpi`, {
      command,
    }),
  scan: (ip: string, port: number) => post("/api/scan", { ip, port }),
  configure: (payload: {
    source: SourceFunc;
    level: number;
    compliance: number;
    auto_range: boolean;
    wire: WireMode;
    sync_mode?: SyncMode;
  }) => post("/api/configure", payload),
  measure: () => post("/api/measure"),
  streamStart: (interval_ms: number) => post("/api/stream/start", { interval_ms }),
  streamStop: () => post("/api/stream/stop"),
  sweep: (payload: Record<string, unknown>) => post("/api/jobs/sweep", payload),
  pulse: (payload: Record<string, unknown>) => post("/api/jobs/pulse", payload),
  stopJob: () => post("/api/jobs/stop"),
  log: (limit = 400) => get<{ entries: unknown[] }>(`/api/log?limit=${limit}`),
  newSession: (name: string) => post(`/api/session/new?name=${encodeURIComponent(name)}`),
  download: async (kind: string, fallback: string) => {
    const response = await fetch(`/api/session/export/${kind}`);
    if (!response.ok) throw new Error(`Export failed: ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fallback;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },
};
