/**
 * Helm's money format (docs/helm/VISUAL.md "Money"): INR amounts use Indian digit grouping (en-IN)
 * in every language, ₹6,59,440.64 and ₹32,44,597.52, because Helm's own sentences say "lakh" and
 * "crore" and a figure grouped in thousands beside "₹6.6 lakh" reads wrong. Every other currency
 * follows the person's language (N-064). Plain TypeScript, no React: `npm run check:self` runs
 * runHelmMoneySelfCheck outside the browser.
 */
import { applyDisplayPreferences, formatMoney, formatMoneyInLocale, getDisplayPreferences } from "@/utils/displayTime";
import { formatMoneyMicros } from "@/utils/formatters";

const isInr = (currency: string | null | undefined): boolean => (currency ?? "").toUpperCase() === "INR";

/** An amount in millionths of its currency: Indian grouping for rupees, the person's language otherwise. */
export const formatHelmMoney = (
  micros: number | null | undefined,
  currency: string | null | undefined,
  options: { signed?: boolean } = {},
): string => {
  if (!isInr(currency) || micros === null || micros === undefined || !Number.isFinite(micros)) {
    return formatMoneyMicros(micros, currency, options);
  }
  return formatMoneyInLocale(micros / 1_000_000, "INR", "en-IN", {
    maximumFractionDigits: 2,
    signDisplay: options.signed ? "exceptZero" : "auto",
  });
};

/**
 * A short axis or tile figure: "₹6.6L" and "₹1.2Cr" for rupees, "$1.2M" or "1,2 Mio. €" otherwise.
 * Rupees below a lakh are written out ("₹1,453"): Indian English's compact form for thousands is
 * "₹1.5T", which reads as trillions.
 */
export const formatHelmMoneyCompact = (micros: number, currency: string): string => {
  const amount = micros / 1_000_000;
  if (!isInr(currency)) {
    return formatMoney(amount, currency, { notation: "compact", maximumFractionDigits: 1 });
  }
  return Math.abs(amount) < 100_000
    ? formatMoneyInLocale(amount, "INR", "en-IN", { maximumFractionDigits: 0 })
    : formatMoneyInLocale(amount, "INR", "en-IN", { notation: "compact", maximumFractionDigits: 1 });
};

export const runHelmMoneySelfCheck = (): string[] => {
  const failures: string[] = [];
  const saved = getDisplayPreferences();
  const expect = (label: string, actual: string, wanted: string | RegExp): void => {
    const ok = typeof wanted === "string" ? actual === wanted : wanted.test(actual);
    if (!ok) {
      failures.push(`${label}: got ${JSON.stringify(actual)}, wanted ${String(wanted)}`);
    }
  };
  try {
    applyDisplayPreferences(undefined, "en-US");
    expect("rupees in lakh grouping for an English (US) reader", formatHelmMoney(659_440_640_000, "INR"), "₹6,59,440.64");
    expect("rupees in crore grouping", formatHelmMoney(32_445_975_200_000, "INR"), "₹3,24,45,975.20");
    expect("a signed rupee change", formatHelmMoney(-1_200_000_000_000, "INR", { signed: true }), "-₹12,00,000.00");
    expect("dollars keep the reader's language", formatHelmMoney(1_234_567_500_000, "USD"), "$1,234,567.50");
    applyDisplayPreferences(undefined, "de-DE");
    expect("rupees in lakh grouping for a German reader", formatHelmMoney(659_440_640_000, "INR"), "₹6,59,440.64");
    expect("euros keep the reader's language", formatHelmMoney(1_234_500_000, "EUR"), /^1\.234,50\s€$/);
    expect("nothing stays a dash", formatHelmMoney(null, "INR"), "—");
    expect("a compact rupee figure", formatHelmMoneyCompact(659_440_640_000, "INR"), /^₹6\.6\s?L/);
    expect("a compact rupee figure below a lakh is written out", formatHelmMoneyCompact(1_453_050_000, "INR"), "₹1,453");
  } finally {
    applyDisplayPreferences(saved.timeZone, saved.locale);
  }
  return failures;
};
