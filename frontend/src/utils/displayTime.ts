/**
 * ARCH-30 Tranche 3 (D-5) — timestamps follow each person's profile.
 *
 * D-5 assigns timestamp display and language to the User Profile. Before this
 * module, 35 call sites formatted timestamps with `toLocaleString()` and
 * friends, which use the BROWSER's timezone and language; the profile's
 * `timezone` and `locale` were stored, editable and read by nothing.
 *
 * `DisplayPreferencesBoundary` loads the profile and calls
 * `applyDisplayPreferences`; every formatter here reads the result. Timestamps
 * carry a short zone name ("10:05 IST") so a reader never has to guess which
 * clock a time is on. An invalid stored zone or locale falls back to the
 * browser rather than throwing a RangeError into a render.
 */

export interface DisplayPreferences {
  readonly timeZone?: string | undefined;
  readonly locale?: string | undefined;
}

type TimestampInput = string | number | Date | null | undefined;

let current: DisplayPreferences = {};

const validTimeZone = (value?: string | null): string | undefined => {
  if (!value) {
    return undefined;
  }
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value });
    return value;
  } catch {
    return undefined;
  }
};

const validLocale = (value?: string | null): string | undefined => {
  if (!value) {
    return undefined;
  }
  try {
    return Intl.getCanonicalLocales(value)[0];
  } catch {
    return undefined;
  }
};

/** Apply profile preferences. Returns a key that changes when they change. */
export const applyDisplayPreferences = (
  timeZone?: string | null,
  locale?: string | null,
): string => {
  current = { timeZone: validTimeZone(timeZone), locale: validLocale(locale) };
  return `${current.timeZone ?? "browser"}|${current.locale ?? "browser"}`;
};

export const getDisplayPreferences = (): DisplayPreferences => current;

const toDate = (value: TimestampInput): Date | null => {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

/** Date and time, with the zone: "11 Sept 2026, 3:05 pm IST". */
export const formatTimestamp = (value: TimestampInput, fallback = "—"): string => {
  const date = toDate(value);
  if (!date) {
    return fallback;
  }
  return new Intl.DateTimeFormat(current.locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: current.timeZone,
    timeZoneName: "short",
  }).format(date);
};

/** The calendar day an instant falls on in the reader's zone: "11 Sept 2026". */
export const formatTimestampDate = (value: TimestampInput, fallback = "—"): string => {
  const date = toDate(value);
  if (!date) {
    return fallback;
  }
  return new Intl.DateTimeFormat(current.locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: current.timeZone,
  }).format(date);
};

/** Time of day only, for places that already show the date. */
export const formatTimestampTime = (
  value: TimestampInput,
  options: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" },
  fallback = "",
): string => {
  const date = toDate(value);
  if (!date) {
    return fallback;
  }
  return new Intl.DateTimeFormat(current.locale, {
    ...options,
    timeZone: current.timeZone,
  }).format(date);
};
