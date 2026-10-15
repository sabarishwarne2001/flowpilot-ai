import React, { useEffect, useRef, useState } from "react";

import type { Readback, ReadbackPart } from "@/types/helm";

/**
 * A mandate's readback with its chips editable in place (brief §5.1): "Keep INR cash above
 * [₹5 lakh] on every payment-run day, with at least [90%] confidence." Pressing a chip opens a small
 * editor for that one value; the parent re-runs the validator and shows the new sentence. Booleans
 * flip on a press. Chips that are not parameters of their own (a floor's currency, which belongs to
 * the floor) stay plain.
 */

type Params = Record<string, unknown>;

interface Money {
  amount_micros: number;
  currency: string;
}

const EDITABLE = new Set(["money", "percent", "days", "count", "bool", "list"]);

const isMoney = (value: unknown): value is Money =>
  typeof value === "object" && value !== null && "amount_micros" in value && "currency" in value;

/** Micros to a plain decimal string without float noise ("500000", "1250.5"). */
export const fromMicros = (micros: number): string => {
  const whole = Math.trunc(micros / 1_000_000);
  const fraction = String(Math.abs(micros % 1_000_000)).padStart(6, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
};

/** "5,00,000.50" or "500000.5" to micros; null when it is not a non-negative number. */
export const toMicros = (text: string): number | null => {
  const clean = text.replace(/[,\s_]/g, "");
  if (!/^\d+(\.\d{0,6})?$/.test(clean)) {
    return null;
  }
  const [whole, fraction = ""] = clean.split(".");
  return Number(whole) * 1_000_000 + Number(fraction.padEnd(6, "0"));
};

const initialText = (part: ReadbackPart): string => {
  const value = part.value;
  if (part.kind === "money" && isMoney(value)) {
    return fromMicros(value.amount_micros);
  }
  if (part.kind === "list" && Array.isArray(value)) {
    return value.join(", ");
  }
  return value === null || value === undefined ? "" : String(value);
};

/** The new parameter value from the editor's text, or a plain-words problem. */
const parse = (part: ReadbackPart, text: string, currency: string): { value?: unknown; problem?: string } => {
  const trimmed = text.trim();
  switch (part.kind) {
    case "money": {
      const micros = toMicros(trimmed);
      if (micros === null) {
        return { problem: "Write an amount, for example 500000 or 2,50,000." };
      }
      if (!/^[A-Za-z]{3}$/.test(currency.trim())) {
        return { problem: "Write the currency as three letters, for example INR." };
      }
      return { value: { amount_micros: micros, currency: currency.trim().toUpperCase() } };
    }
    case "percent":
      return /^\d+(\.\d+)?$/.test(trimmed) ? { value: trimmed } : { problem: "Write a percentage, for example 2 or 1.5." };
    case "days":
    case "count":
      return /^\d+$/.test(trimmed) ? { value: Number(trimmed) } : { problem: "Write a whole number." };
    case "list": {
      const items = trimmed.split(/[,\s]+/).filter(Boolean);
      return items.length > 0 && items.every((item) => /^\d+$/.test(item))
        ? { value: items.map(Number) }
        : { problem: "Write whole numbers separated by commas, for example 3, 10, 20." };
    }
    default:
      return { value: trimmed };
  }
};

const ChipEditor: React.FC<{
  readonly part: ReadbackPart;
  readonly onSet: (value: unknown) => void;
  readonly onClose: () => void;
}> = ({ part, onSet, onClose }) => {
  const [text, setText] = useState(initialText(part));
  const [currency, setCurrency] = useState(isMoney(part.value) ? part.value.currency : "");
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const label = `New value for ${part.display ?? part.chip}`;

  const apply = (): void => {
    const parsed = parse(part, text, currency);
    if (parsed.problem) {
      setProblem(parsed.problem);
      return;
    }
    onSet(parsed.value);
  };

  return (
    <span
      className="helm-panel mx-1 inline-flex flex-wrap items-center gap-1.5 px-2 py-1 align-middle"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onClose();
        }
      }}
    >
      <input
        ref={input}
        className="helm-input helm-figure w-28 py-0.5 text-sm"
        aria-label={label}
        value={text}
        inputMode={part.kind === "list" ? "text" : "decimal"}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            apply();
          }
        }}
      />
      {part.kind === "money" ? (
        <input
          className="helm-input helm-figure w-16 py-0.5 text-sm uppercase"
          aria-label="Currency"
          maxLength={3}
          value={currency}
          onChange={(event) => setCurrency(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              apply();
            }
          }}
        />
      ) : null}
      <button type="button" className="helm-btn helm-btn-primary py-0.5" onClick={apply}>
        Set
      </button>
      <button type="button" className="helm-btn py-0.5" onClick={onClose}>
        Cancel
      </button>
      {problem ? (
        <span role="alert" className="w-full text-xs text-red-600 dark:text-red-400">
          {problem}
        </span>
      ) : null}
    </span>
  );
};

export const EditableReadback: React.FC<{
  readonly readback: Readback | null | undefined;
  readonly params: Params;
  /** Absent: read-only (the same look as ReadbackSentence). */
  readonly onChange?: (param: string, value: unknown) => void;
  readonly disabled?: boolean;
  readonly className?: string;
}> = ({ readback, params, onChange, disabled = false, className = "" }) => {
  const [open, setOpen] = useState<string | null>(null);
  if (!readback) {
    return null;
  }
  return (
    <p className={`leading-loose ${className}`} data-testid="helm-readback">
      {readback.parts.map((part, index) => {
        if (part.chip === undefined) {
          return <React.Fragment key={index}>{part.text}</React.Fragment>;
        }
        const display = part.display ?? String(part.value ?? "");
        const editable = Boolean(onChange) && EDITABLE.has(part.kind ?? "") && part.chip in params;
        if (!editable) {
          return (
            <span key={index} className="helm-chip helm-chip-accent helm-figure mx-0.5 align-baseline">
              {display}
            </span>
          );
        }
        if (open === part.chip) {
          return (
            <ChipEditor
              key={index}
              part={part}
              onClose={() => setOpen(null)}
              onSet={(value) => {
                setOpen(null);
                onChange?.(part.chip as string, value);
              }}
            />
          );
        }
        return (
          <button
            key={index}
            type="button"
            disabled={disabled}
            className="helm-chip helm-chip-accent helm-figure mx-0.5 cursor-pointer align-baseline underline decoration-dotted underline-offset-4 hover:opacity-90 disabled:cursor-not-allowed"
            aria-label={part.kind === "bool" ? `Change: ${display}` : `Change ${display}`}
            onClick={() => (part.kind === "bool" ? onChange?.(part.chip as string, !part.value) : setOpen(part.chip as string))}
          >
            {display}
          </button>
        );
      })}
    </p>
  );
};

export default EditableReadback;
