import type { ReactNode } from "react";
import { siParts } from "../lib/format";

interface ReadoutProps {
  label: string;
  value: number | null | undefined;
  unit: string;
  accent?: string;
  digits?: number;
  footer?: ReactNode;
  size?: "lg" | "md";
}

export default function Readout({
  label,
  value,
  unit,
  accent = "var(--text)",
  digits = 4,
  footer,
  size = "lg",
}: ReadoutProps) {
  const parts = siParts(value, unit, digits);
  return (
    <div className={`readout readout-${size}`}>
      <div className="readout-label">
        <span>{label}</span>
        <span className="readout-unit">{unit}</span>
      </div>
      <div className="readout-value" style={{ color: accent }}>
        <span className="readout-number">{parts.number}</span>
        {parts.prefix ? <span className="readout-prefix">{parts.prefix}</span> : null}
      </div>
      {footer ? <div className="readout-footer">{footer}</div> : null}
    </div>
  );
}

interface StatRowProps {
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
  count: number;
  unit: string;
}

export function StatRow({ min, max, mean, std, count, unit }: StatRowProps) {
  const fmt = (value: number | null) =>
    value === null ? "—" : siParts(value, unit, 3).number + (siParts(value, unit, 3).prefix || "");
  return (
    <div className="stat-row">
      <span>
        <em>min</em> {fmt(min)}
      </span>
      <span>
        <em>max</em> {fmt(max)}
      </span>
      <span>
        <em>mean</em> {fmt(mean)}
      </span>
      <span>
        <em>σ</em> {fmt(std)}
      </span>
      <span className="stat-count">n={count}</span>
    </div>
  );
}
