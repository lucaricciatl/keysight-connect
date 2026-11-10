import { useEffect, useState } from "react";

export interface UnitOption {
  label: string;
  scale: number;
}

interface Props {
  label?: string;
  value: number;
  onChange: (value: number) => void;
  units?: UnitOption[];
  disabled?: boolean;
  hint?: string;
  width?: string;
}

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "";
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude >= 1e9 || magnitude < 1e-6)) {
    return value.toExponential(4).replace(/\.?0+e/, "e");
  }
  return String(Number(value.toPrecision(7)));
}

export default function NumberField({
  label,
  value,
  onChange,
  units,
  disabled,
  hint,
  width,
}: Props) {
  const options = units && units.length ? units : [{ label: "", scale: 1 }];
  const [unitIndex, setUnitIndex] = useState(0);
  const unit = options[Math.min(unitIndex, options.length - 1)];
  const [text, setText] = useState(() => formatNumber(value / unit.scale));
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setText(formatNumber(value / unit.scale));
  }, [value, unit.scale, focused]);

  const commit = (raw: string) => {
    const parsed = Number.parseFloat(raw.replace(",", "."));
    if (!Number.isFinite(parsed)) return;
    onChange(parsed * unit.scale);
  };

  return (
    <div className="field" style={width ? { width } : undefined}>
      {label ? <label className="field-label">{label}</label> : null}
      <div className={`input-wrap${disabled ? " is-disabled" : ""}`}>
        <input
          inputMode="decimal"
          spellCheck={false}
          value={text}
          disabled={disabled}
          onChange={(event) => {
            setText(event.target.value);
            commit(event.target.value);
          }}
          onFocus={(event) => {
            setFocused(true);
            event.currentTarget.select();
          }}
          onBlur={() => {
            setFocused(false);
            setText(formatNumber(value / unit.scale));
          }}
        />
        {options.length > 1 ? (
          <select
            className="unit-select"
            value={unitIndex}
            disabled={disabled}
            onChange={(event) => setUnitIndex(Number(event.target.value))}
          >
            {options.map((option, index) => (
              <option key={`${option.label}-${index}`} value={index}>
                {option.label}
              </option>
            ))}
          </select>
        ) : (
          <span className="unit">{unit.label}</span>
        )}
      </div>
      {hint ? <span className="field-hint">{hint}</span> : null}
    </div>
  );
}
