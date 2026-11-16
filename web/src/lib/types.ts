export type DeviceLetter = "A" | "B";
export type SourceFunc = "VOLT" | "CURR";
export type SyncMode = "trigger" | "parallel" | "sequential";
export type Quantity = "volts" | "amps" | "ohms" | "watts";
export type WireMode = "2W" | "4W";

export interface Metric {
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
  count: number;
}

export interface Stats {
  n: number;
  volts: Metric;
  amps: Metric;
  ohms: Metric;
  watts: Metric;
}

export interface Reading {
  time: string;
  volts: number;
  amps: number;
  ohms: number | null;
  watts: number | null;
}

export interface Device {
  letter: DeviceLetter;
  connected: boolean;
  address: string;
  transport: string;
  port: number;
  timeout_ms: number;
  idn: string;
  output: boolean;
  four_wire: boolean;
  wire: WireMode;
  last: Reading | null;
  stats: Stats;
}

export interface Settings {
  source: SourceFunc;
  level: number;
  compliance: number;
  auto_range: boolean;
  four_wire: boolean;
  wire: WireMode;
  sync_mode: SyncMode;
}

export type JobState = "idle" | "running" | "done" | "error";

export interface Job {
  id?: string;
  kind?: string;
  state: JobState;
  progress: number;
  total: number;
  started?: string;
  message?: string;
  x?: number;
}

export interface StreamState {
  running: boolean;
  interval_ms: number;
}

export interface SessionFile {
  name: string;
  size: number;
}

export interface Session {
  id: string;
  name: string;
  dir: string;
  created: string;
  counts: { measurements: number; sweep_points: number; pulse_points: number };
  files: SessionFile[];
}

export interface AppState {
  devices: Record<DeviceLetter, Device>;
  settings: Settings;
  stream: StreamState;
  job: Job;
  session: Session | null;
  server: { started: number; data_dir: string };
  last_sweep?: LastSweep | null;
  last_pulse?: LastPulse | null;
}

export interface LogEntry {
  time: string;
  device: string | null;
  text: string;
  level: "tx" | "rx" | "info" | "err";
}

export interface SeriesRow {
  device: DeviceLetter;
  index: number;
  x: number;
  volts: number;
  amps: number;
  ohms: number | null;
  watts: number | null;
}

export interface LastSweep {
  run_id: string;
  source: SourceFunc;
  params: {
    start: number;
    stop: number;
    points: number;
    delay_ms: number;
    compliance: number;
  };
  series: Record<DeviceLetter, SeriesRow[]>;
  aborted: boolean;
}

export interface LastPulse {
  run_id: string;
  source: SourceFunc;
  params: {
    peak: number;
    base: number;
    width_ms: number;
    period_ms: number;
    count: number;
  };
  series: Record<DeviceLetter, { values: number[]; stats: PulseSummary; rows: PulseRow[] }>;
  skew_ms: number;
  aborted: boolean;
}

export interface SweepState {
  runId: string | null;
  source: SourceFunc;
  running: boolean;
  aborted: boolean;
  rows: Record<DeviceLetter, SeriesRow[]>;
  params: {
    start: number;
    stop: number;
    points: number;
    delay_ms: number;
    compliance: number;
  } | null;
}

export interface PulseRow {
  index: number;
  measured: number;
  volts: number;
  amps: number;
  ohms: number | null;
}

export interface PulseSummary {
  n: number;
  mean: number | null;
  min: number | null;
  max: number | null;
  std: number | null;
}

export interface PulseState {
  runId: string | null;
  source: SourceFunc;
  running: boolean;
  aborted: boolean;
  rows: Record<DeviceLetter, PulseRow[]>;
  stats: Record<DeviceLetter, PulseSummary | null>;
  skew_ms: number | null;
  params: {
    peak: number;
    base: number;
    width_ms: number;
    period_ms: number;
    count: number;
  } | null;
}

export interface ConnectionConfig {
  address: string;
  transport: string;
  port: number;
  timeout_ms: number;
}
