/**
 * HARDENING-T2:Phase3 — a searchable, validated IANA timezone field.
 *
 * Replaces free-text inputs where typing "jnkjnkjn" produced a generic save
 * error. The list is the browser's own `Intl.supportedValuesOf("timeZone")`,
 * searched through a native datalist, and a value outside it is flagged
 * inline before anything is sent. The server still validates (IANA list).
 */
import React, { useMemo } from "react";

export const TIMEZONES: string[] = (() => {
  try {
    const values = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.(
      "timeZone",
    );
    if (values && values.length > 0) {
      return values.includes("UTC") ? values : ["UTC", ...values];
    }
  } catch {
    /* older engines fall through */
  }
  return ["UTC", "Asia/Kolkata", "America/New_York", "Europe/London", "Asia/Singapore", "Australia/Sydney"];
})();

const TIMEZONE_SET = new Set(TIMEZONES);

export function isKnownTimezone(value: string): boolean {
  return value.trim() === "" || TIMEZONE_SET.has(value.trim());
}

interface TimezoneFieldProps {
  readonly id: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly disabled?: boolean;
  readonly allowEmpty?: boolean;
}

export const TimezoneField: React.FC<TimezoneFieldProps> = ({ id, value, onChange, disabled, allowEmpty = true }) => {
  const listId = `${id}-options`;
  const invalid = useMemo(
    () => (value.trim() === "" ? !allowEmpty : !TIMEZONE_SET.has(value.trim())),
    [value, allowEmpty],
  );
  return (
    <>
      <input
        id={id}
        list={listId}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Search, e.g. Asia/Kolkata"
        aria-invalid={invalid}
        autoComplete="off"
        spellCheck={false}
        className={`mt-1 w-full rounded-lg border bg-background px-3 py-1.5 font-mono text-sm text-foreground focus:outline-none disabled:opacity-50 ${
          invalid ? "border-destructive focus:border-destructive" : "border-border focus:border-primary"
        }`}
      />
      <datalist id={listId}>
        {TIMEZONES.map((zone) => (
          <option key={zone} value={zone} />
        ))}
      </datalist>
      {invalid && (
        <p className="mt-1 text-xs text-destructive" role="alert">
          Choose a timezone from the list (IANA name, e.g. Asia/Kolkata).
        </p>
      )}
    </>
  );
};

/** A curated set of locales FlowPilot formats well. */
export const LOCALES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "en-US", label: "English (United States)" },
  { value: "en-GB", label: "English (United Kingdom)" },
  { value: "en-IN", label: "English (India)" },
  { value: "en-AU", label: "English (Australia)" },
  { value: "en-SG", label: "English (Singapore)" },
  { value: "hi-IN", label: "Hindi (India)" },
  { value: "ta-IN", label: "Tamil (India)" },
  { value: "fr-FR", label: "French (France)" },
  { value: "de-DE", label: "German (Germany)" },
  { value: "es-ES", label: "Spanish (Spain)" },
  { value: "es-MX", label: "Spanish (Mexico)" },
  { value: "pt-BR", label: "Portuguese (Brazil)" },
  { value: "it-IT", label: "Italian (Italy)" },
  { value: "nl-NL", label: "Dutch (Netherlands)" },
  { value: "ja-JP", label: "Japanese (Japan)" },
  { value: "zh-CN", label: "Chinese (Simplified)" },
  { value: "ar-AE", label: "Arabic (UAE)" },
];

export default TimezoneField;
