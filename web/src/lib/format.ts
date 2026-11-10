const PREFIXES: { exp: number; sym: string }[] = [
  { exp: 12, sym: "T" },
  { exp: 9, sym: "G" },
  { exp: 6, sym: "M" },
  { exp: 3, sym: "k" },
  { exp: 0, sym: "" },
  { exp: -3, sym: "m" },
  { exp: -6, sym: "µ" },
  { exp: -9, sym: "n" },
  { exp: -12, sym: "p" },
  { exp: -15, sym: "f" },
];

/** Engineering notation: 0.0049999 -> "5.000 mA". */
export function si(value: number | null | undefined, unit = "", sig = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value === 0) return unit ? `0 ${unit}` : "0";
  const abs = Math.abs(value);
  const exp = Math.min(12, Math.max(-15, Math.floor(Math.log10(abs) / 3) * 3));
  const prefix = PREFIXES.find((item) => item.exp === exp) ?? { exp: 0, sym: "" };
  const mantissa = value / Math.pow(10, prefix.exp);
  let text = mantissa.toPrecision(sig);
  if (text.includes("e")) text = mantissa.toFixed(2);
  text = text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
  return `${text}${prefix.sym ? " " + prefix.sym : " "}${unit}`.replace(/\s+/g, " ").trim();
}

/** Split a value into mantissa, prefix and unit so the UI can style them apart. */
export function siParts(value: number | null | undefined, unit = "", sig = 4) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return { number: "—", prefix: "", unit };
  }
  const text = si(value, unit, sig);
  const match = text.match(/^(-?[\d.]+)\s*(T|G|M|k|m|µ|n|p|f)?\s*(.*)$/);
  if (!match) return { number: text, prefix: "", unit: "" };
  return { number: match[1], prefix: match[2] ?? "", unit: match[3] ?? "" };
}

const SOURCE_UNITS: Record<string, string> = {
  VOLT: "V",
  CURR: "A",
};

export function sourceUnit(source: string): string {
  return SOURCE_UNITS[source] ?? "";
}

export function measureFunc(source: string): "volts" | "amps" {
  return source === "VOLT" ? "amps" : "volts";
}

export function measureUnit(source: string): string {
  return source === "VOLT" ? "A" : "V";
}

export function quantityUnit(quantity: "volts" | "amps" | "ohms" | "watts"): string {
  return { volts: "V", amps: "A", ohms: "Ω", watts: "W" }[quantity];
}

export function quantityLabel(quantity: "volts" | "amps" | "ohms" | "watts"): string {
  return { volts: "Voltage", amps: "Current", ohms: "Resistance", watts: "Power" }[quantity];
}

export function compact(value: number | null | undefined, sig = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return si(value, "", sig);
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString([], { hour12: false });
}

export function shortIdn(idn: string): string {
  if (!idn) return "not connected";
  const parts = idn.split(",");
  if (parts.length >= 3) return `${parts[1]} · ${parts[2]}`;
  return idn;
}
