import { useEffect, useMemo, useRef, useState } from "react";

export interface PlotPoint {
  x: number;
  y: number;
}

export interface PlotSeries {
  name: string;
  color: string;
  points: PlotPoint[];
  dashed?: boolean;
}

interface Props {
  series: PlotSeries[];
  xLabel: string;
  yLabel: string;
  logY?: boolean;
  height?: number;
  empty?: string;
  xFormat?: (value: number) => string;
  yFormat?: (value: number) => string;
}

const PAD = { left: 76, right: 18, top: 16, bottom: 44 };
const GRID = "rgba(255,255,255,0.055)";
const AXIS = "rgba(255,255,255,0.18)";
const TEXT = "#7f8f9f";

function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || count < 1) return [];
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const span = max - min;
  const rough = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const start = Math.ceil(min / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let value = start; value <= max + step * 1e-6; value += step) {
    ticks.push(Number(value.toPrecision(12)));
    if (ticks.length > 24) break;
  }
  return ticks;
}

function logTicks(min: number, max: number): number[] {
  const lo = Math.floor(Math.log10(Math.max(min, Number.MIN_VALUE)));
  const hi = Math.ceil(Math.log10(Math.max(max, Number.MIN_VALUE)));
  const ticks: number[] = [];
  for (let exp = lo; exp <= hi; exp += 1) {
    ticks.push(Math.pow(10, exp));
    if (hi - lo <= 3) {
      ticks.push(2 * Math.pow(10, exp), 5 * Math.pow(10, exp));
    }
  }
  return ticks;
}

function defaultFormat(value: number): string {
  if (value === 0) return "0";
  const abs = Math.abs(value);
  if (abs >= 1e5 || abs < 1e-3) return value.toExponential(1).replace("e", "e");
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : 3;
  return value.toFixed(digits);
}

export default function Plot({
  series,
  xLabel,
  yLabel,
  logY = false,
  height = 300,
  empty = "no data yet",
  xFormat = defaultFormat,
  yFormat = defaultFormat,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<{ px: number; py: number } | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) setWidth(box.width);
    });
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);

  const hasData = series.some((s) => s.points.some((p) => Number.isFinite(p.y)));

  const domain = useMemo(() => {
    let xmin = Infinity;
    let xmax = -Infinity;
    let ymin = Infinity;
    let ymax = -Infinity;
    for (const line of series) {
      for (const point of line.points) {
        if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
        if (logY && point.y <= 0) continue;
        xmin = Math.min(xmin, point.x);
        xmax = Math.max(xmax, point.x);
        ymin = Math.min(ymin, point.y);
        ymax = Math.max(ymax, point.y);
      }
    }
    if (!Number.isFinite(xmin) || !Number.isFinite(ymin)) return null;
    if (xmin === xmax) {
      xmin -= 1;
      xmax += 1;
    }
    if (ymin === ymax) {
      const pad = Math.abs(ymin) * 0.1 || 1;
      ymin -= pad;
      ymax += pad;
    }
    if (logY) {
      return { xmin, xmax, ymin, ymax, lyMin: Math.log10(ymin), lyMax: Math.log10(ymax) };
    }
    const pad = (ymax - ymin) * 0.07;
    return { xmin, xmax, ymin: ymin - pad, ymax: ymax + pad, lyMin: 0, lyMax: 0 };
  }, [series, logY]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(width, 120);
    const h = height;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const plotW = Math.max(w - PAD.left - PAD.right, 10);
    const plotH = Math.max(h - PAD.top - PAD.bottom, 10);

    if (!domain) {
      ctx.fillStyle = TEXT;
      ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textAlign = "center";
      ctx.fillText(empty, PAD.left + plotW / 2, PAD.top + plotH / 2);
      return;
    }

    const toX = (value: number) =>
      PAD.left + ((value - domain.xmin) / (domain.xmax - domain.xmin)) * plotW;
    const toY = (value: number) => {
      if (logY) {
        const ly = Math.log10(Math.max(value, Number.MIN_VALUE));
        return PAD.top + plotH - ((ly - domain.lyMin) / (domain.lyMax - domain.lyMin)) * plotH;
      }
      return PAD.top + plotH - ((value - domain.ymin) / (domain.ymax - domain.ymin)) * plotH;
    };

    // grid + ticks
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillStyle = TEXT;
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 1;

    const xTicks = niceTicks(domain.xmin, domain.xmax, 6);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const tick of xTicks) {
      const px = Math.round(toX(tick)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(px, PAD.top);
      ctx.lineTo(px, PAD.top + plotH);
      ctx.stroke();
      ctx.fillText(xFormat(tick), px, PAD.top + plotH + 8);
    }

    const yTicks = logY
      ? logTicks(Math.pow(10, domain.lyMin), Math.pow(10, domain.lyMax))
      : niceTicks(domain.ymin, domain.ymax, 5);
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const tick of yTicks) {
      if (logY && tick <= 0) continue;
      const py = toY(tick);
      if (py < PAD.top - 2 || py > PAD.top + plotH + 2) continue;
      const y = Math.round(py) + 0.5;
      ctx.beginPath();
      ctx.moveTo(PAD.left, y);
      ctx.lineTo(PAD.left + plotW, y);
      ctx.stroke();
      ctx.fillText(yFormat(tick), PAD.left - 10, y);
    }

    // axes
    ctx.strokeStyle = AXIS;
    ctx.beginPath();
    ctx.moveTo(PAD.left + 0.5, PAD.top);
    ctx.lineTo(PAD.left + 0.5, PAD.top + plotH + 0.5);
    ctx.lineTo(PAD.left + plotW, PAD.top + plotH + 0.5);
    ctx.stroke();

    // series
    for (const line of series) {
      const points = line.points.filter(
        (p) => Number.isFinite(p.x) && Number.isFinite(p.y) && (!logY || p.y > 0),
      );
      if (!points.length) continue;
      ctx.save();
      ctx.strokeStyle = line.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      if (line.dashed) ctx.setLineDash([5, 4]);
      ctx.beginPath();
      points.forEach((point, index) => {
        const px = toX(point.x);
        const py = toY(point.y);
        if (index === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
      ctx.restore();

      if (points.length <= 220) {
        ctx.fillStyle = line.color;
        for (const point of points) {
          ctx.beginPath();
          ctx.arc(toX(point.x), toY(point.y), 2.2, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    // axis titles
    ctx.fillStyle = TEXT;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText(xLabel.toUpperCase(), PAD.left + plotW / 2, h - 6);
    ctx.save();
    ctx.translate(14, PAD.top + plotH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textBaseline = "top";
    ctx.fillText(yLabel.toUpperCase(), 0, 0);
    ctx.restore();

    // hover crosshair
    if (hover && hasData) {
      let nearest: { x: number; series: PlotSeries; point: PlotPoint } | null = null;
      for (const line of series) {
        for (const point of line.points) {
          if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
          if (logY && point.y <= 0) continue;
          const dx = Math.abs(toX(point.x) - hover.px);
          if (!nearest || dx < Math.abs(toX(nearest.point.x) - hover.px)) {
            nearest = { x: point.x, series: line, point };
          }
        }
      }
      if (nearest) {
        const px = Math.round(toX(nearest.point.x)) + 0.5;
        ctx.strokeStyle = "rgba(255,255,255,0.28)";
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(px, PAD.top);
        ctx.lineTo(px, PAD.top + plotH);
        ctx.stroke();
        ctx.setLineDash([]);
        for (const line of series) {
          const point = line.points.find((p) => Math.abs(p.x - nearest!.point.x) < 1e-12);
          if (!point || !Number.isFinite(point.y)) continue;
          ctx.fillStyle = line.color;
          ctx.beginPath();
          ctx.arc(toX(point.x), toY(point.y), 3.4, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
  }, [domain, hover, width, height, series, logY, xLabel, yLabel, xFormat, yFormat, empty, hasData]);

  const tooltip = useMemo(() => {
    if (!hover || !domain || !hasData) return null;
    const plotW = Math.max(width - PAD.left - PAD.right, 10);
    const ratio = (hover.px - PAD.left) / plotW;
    if (ratio < 0 || ratio > 1) return null;
    const targetX = domain.xmin + ratio * (domain.xmax - domain.xmin);
    const rows: { name: string; color: string; y: number; x: number }[] = [];
    for (const line of series) {
      let best: PlotPoint | null = null;
      for (const point of line.points) {
        if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
        if (!best || Math.abs(point.x - targetX) < Math.abs(best.x - targetX)) best = point;
      }
      if (best) rows.push({ name: line.name, color: line.color, y: best.y, x: best.x });
    }
    if (!rows.length) return null;
    return { x: rows[0].x, rows, left: hover.px, top: hover.py };
  }, [hover, domain, series, width, hasData]);

  return (
    <div className="plot" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          setHover({ px: event.clientX - rect.left, py: event.clientY - rect.top });
        }}
        onMouseLeave={() => setHover(null)}
      />
      {tooltip ? (
        <div
          className="plot-tooltip"
          style={{
            left: Math.min(Math.max(tooltip.left + 14, 8), Math.max(width - 190, 8)),
            top: Math.min(Math.max(tooltip.top - 10, 8), height - 70),
          }}
        >
          <div className="plot-tooltip-x">{xFormat(tooltip.x)}</div>
          {tooltip.rows.map((row) => (
            <div className="plot-tooltip-row" key={row.name}>
              <span className="swatch" style={{ background: row.color }} />
              <span>{row.name}</span>
              <strong>{yFormat(row.y)}</strong>
            </div>
          ))}
        </div>
      ) : null}
      <div className="plot-legend">
        {series.map((line) => (
          <span className="legend-item" key={line.name}>
            <span className="swatch" style={{ background: line.color }} />
            {line.name}
          </span>
        ))}
      </div>
    </div>
  );
}
