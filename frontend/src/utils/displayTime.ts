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

/**
 * Campaign session 3 (N-064) — the workspace's part. A CALENDAR date (a due
 * date, an invoice date) reads in the workspace's date format, the setting that
 * also tells three-way matching how to read that workspace's documents; the
 * workspace's time zone is the clock for a person who never set their own.
 * Numbers and money read in the person's language (`formatNumber`,
 * `formatMoney`); an amount keeps its own currency and never gets a guessed one.
 */
export interface WorkspaceDisplay {
  readonly dateFormat?: string | undefined;
  readonly timeZone?: string | undefined;
}

/** The formats Settings > Workspace > Date format offers. */
export const DATE_FORMATS = ["YYYY-MM-DD", "DD-MM-YYYY", "MM-DD-YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY/MM/DD"] as const;

type TimestampInput = string | number | Date | null | undefined;

let current: DisplayPreferences = {};
let workspace: WorkspaceDisplay = {};

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

/** A zone this browser can show times in (the workspace form's check, F-321). */
export const isUsableTimeZone = (value: string): boolean => validTimeZone(value) !== undefined;

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

/** Apply the open workspace's settings. Returns a key that changes when they change. */
export const applyWorkspaceDisplay = (dateFormat?: string | null, timeZone?: string | null): string => {
  workspace = {
    dateFormat: dateFormat && (DATE_FORMATS as readonly string[]).includes(dateFormat) ? dateFormat : undefined,
    timeZone: validTimeZone(timeZone),
  };
  return `${workspace.dateFormat ?? "locale"}|${workspace.timeZone ?? "none"}`;
};

/** The clock a moment is shown on: the person's zone, else the workspace's, else the browser's. */
const zone = (): string | undefined => current.timeZone ?? workspace.timeZone;

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
    timeZone: zone(),
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
    timeZone: zone(),
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
    timeZone: zone(),
  }).format(date);
};

/**
 * ARCH46-S2:calendar-dates — a CALENDAR date ("2026-02-28": a due date, a
 * holiday), not an instant. It is the same day for every reader, so it is
 * never shifted by a zone (UTC is only the arithmetic frame). Campaign session
 * 3 (N-064): in the workspace's date format ("28/02/2026") when one is set,
 * else in the profile's language ("28 Feb 2026").
 */
export const formatCalendarDate = (iso?: string | null, fallback = "—"): string => {
  if (!iso) {
    return fallback;
  }
  const [y, m, d] = iso.slice(0, 10).split("-").map((x) => Number.parseInt(x, 10));
  if (!y || !m || !d) {
    return iso;
  }
  if (workspace.dateFormat) {
    return workspace.dateFormat
      .replace("YYYY", String(y).padStart(4, "0"))
      .replace("MM", String(m).padStart(2, "0"))
      .replace("DD", String(d).padStart(2, "0"));
  }
  return new Intl.DateTimeFormat(current.locale, {
    year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
  }).format(Date.UTC(y, m - 1, d));
};

/** A calendar month's heading ("February 2028"), in the profile's language, never zone-shifted. */
export const formatCalendarMonth = (year: number, month: number): string =>
  new Intl.DateTimeFormat(current.locale, { month: "long", year: "numeric", timeZone: "UTC" })
    .format(Date.UTC(year, month - 1, 1));

/** A calendar month's short name ("Oct"), in the profile's language, never zone-shifted. */
export const formatCalendarMonthShort = (year: number, month: number): string =>
  new Intl.DateTimeFormat(current.locale, { month: "short", timeZone: "UTC" }).format(Date.UTC(year, month - 1, 1));

/**
 * Campaign session 3 (N-064). Numbers in the person's language ("1,234.5",
 * "1.234,5"): every count, quantity and amount on screen goes through here or
 * `formatMoney`, never `toLocaleString()`, which reads the browser.
 */
export const numberFormat = (options?: Intl.NumberFormatOptions): Intl.NumberFormat => {
  try {
    return new Intl.NumberFormat(current.locale, options);
  } catch {
    return new Intl.NumberFormat(undefined, options);
  }
};

export const formatNumber = (
  value: number | null | undefined,
  options?: Intl.NumberFormatOptions,
  fallback = "—",
): string => (value === null || value === undefined || !Number.isFinite(value) ? fallback : numberFormat(options).format(value));

/**
 * An amount in its own currency, in the person's language: "₹1,234.50",
 * "1.234,50 €". Without a valid ISO currency it is a plain number with two
 * decimals: printing a guessed symbol is worse than printing none (F-172).
 */
export const formatMoney = (
  amount: number | null | undefined,
  currency: string | null | undefined,
  options: Intl.NumberFormatOptions = {},
  fallback = "—",
): string => {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) {
    return fallback;
  }
  if (currency && /^[A-Za-z]{3}$/.test(currency)) {
    try {
      return numberFormat({ style: "currency", currency: currency.toUpperCase(), ...options }).format(amount);
    } catch {
      // an ISO-shaped code the runtime does not know: a plain number below
    }
  }
  return numberFormat({ minimumFractionDigits: 2, maximumFractionDigits: 2, ...options }).format(amount);
};

/**
 * Helm visual session: money in a fixed locale's grouping, whatever the person's language. Helm
 * prints rupees with Indian grouping (₹6,59,440.64) because its sentences say lakh and crore
 * (components/helm/money.ts); nothing else should need it.
 */
export const formatMoneyInLocale = (
  amount: number,
  currency: string,
  locale: string,
  options: Intl.NumberFormatOptions = {},
): string => {
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency, ...options }).format(amount);
  } catch {
    return formatMoney(amount, currency, options);
  }
};

/**
 * "5 minutes ago", "yesterday", "3 months ago", in the person's language.
 *
 * Website and payments session: this lived in utils/formatters.ts and passed
 * `navigator.language` straight to Intl.RelativeTimeFormat, which throws on a tag
 * like "en-US@posix" (a POSIX locale suffix some systems report) and took the whole
 * Helm cockpit down. It now reads the validated preference like every other format,
 * and an unusable language falls back to the runtime's default.
 */
export const formatRelativeTime = (dateInput: TimestampInput, now: number = Date.now()): string => {
  if (dateInput === null || dateInput === undefined) {
    return "—";
  }
  const date = new Date(dateInput);
  if (Number.isNaN(date.getTime())) {
    return "Invalid Date";
  }
  let formatter: Intl.RelativeTimeFormat;
  try {
    formatter = new Intl.RelativeTimeFormat(current.locale, { numeric: "auto" });
  } catch {
    formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  }
  const seconds = Math.floor((now - date.getTime()) / 1000);
  if (seconds < 60) {
    return formatter.format(-seconds, "second");
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return formatter.format(-minutes, "minute");
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return formatter.format(-hours, "hour");
  }
  const days = Math.floor(hours / 24);
  if (days < 30) {
    return formatter.format(-days, "day");
  }
  const months = Math.floor(days / 30);
  if (months < 12) {
    return formatter.format(-months, "month");
  }
  return formatter.format(-Math.floor(months / 12), "year");
};

/**
 * Campaign session 3 (N-064). Run by `npm run check:self`: the rules above,
 * exercised with a German reader and a DD/MM/YYYY workspace on India's clock.
 * Restores the previous preferences before returning.
 */
export const runDisplayFormatSelfCheck = (): string[] => {
  const failures: string[] = [];
  const saved = { current, workspace };
  const expect = (label: string, actual: string, wanted: string | RegExp): void => {
    const ok = typeof wanted === "string" ? actual === wanted : wanted.test(actual);
    if (!ok) {
      failures.push(`${label}: got ${JSON.stringify(actual)}, wanted ${String(wanted)}`);
    }
  };
  try {
    applyDisplayPreferences(undefined, "de-DE");
    applyWorkspaceDisplay("DD/MM/YYYY", "Asia/Kolkata");
    expect("a number in the person's language", formatNumber(1234.5), "1.234,5");
    expect("money in its own currency", formatMoney(1234.5, "EUR"), /^1\.234,50\s€$/);
    expect("no guessed currency", formatMoney(12, null), "12,00");
    expect("a calendar date in the workspace's format", formatCalendarDate("2026-12-05"), "05/12/2026");
    expect("the workspace's clock when the person has none", formatTimestampTime("2026-01-01T00:00:00Z", { hour: "2-digit", minute: "2-digit" }), "05:30");
    applyDisplayPreferences("Europe/Berlin", "de-DE");
    expect("the person's own clock wins", formatTimestampTime("2026-01-01T00:00:00Z", { hour: "2-digit", minute: "2-digit" }), "01:00");
    applyWorkspaceDisplay("not-a-format", undefined);
    expect("an unknown date format falls back to the language", formatCalendarDate("2026-12-05"), /^5\.\s?Dez\.?\s2026$/);
    expect("relative time in the person's language", formatRelativeTime(Date.UTC(2026, 0, 1), Date.UTC(2026, 0, 1, 0, 5)), "vor 5 Minuten");
    applyDisplayPreferences(undefined, "en-US@posix");
    expect("a language tag Intl refuses falls back, never throws", formatRelativeTime(Date.UTC(2026, 0, 1), Date.UTC(2026, 0, 2)), /\S/);
  } finally {
    current = saved.current;
    workspace = saved.workspace;
  }
  return failures;
};
