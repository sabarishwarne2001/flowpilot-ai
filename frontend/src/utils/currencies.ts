/**
 * Campaign session 3 (F-321) — every currency a workspace can hold.
 *
 * The workspace currency is the fallback three-way matching uses when a document does not state
 * one. The server takes any three-letter ISO 4217 code (F-320); the settings page offered ten,
 * so a workspace in Switzerland or Brazil could not choose its own, and a workspace holding any
 * other code could not save the page at all. The list is the browser's own
 * (`Intl.supportedValuesOf("currency")`), named in the person's language, with the common ones
 * first.
 */

export const COMMON_CURRENCIES = ["USD", "EUR", "GBP", "INR", "AED", "SGD", "AUD", "CAD", "JPY", "CNY"] as const;

const supported = (): string[] => {
  try {
    const values = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("currency");
    if (values && values.length > 0) {
      return values;
    }
  } catch {
    /* older engines fall through */
  }
  return [...COMMON_CURRENCIES];
};

export const isCurrencyCode = (value: string): boolean => /^[A-Z]{3}$/.test(value.trim());

/** "Swiss Franc (CHF)", or the bare code where the browser has no name for it. */
export const currencyLabel = (code: string): string => {
  try {
    const name = new Intl.DisplayNames(undefined, { type: "currency" }).of(code);
    return name && name !== code ? `${name} (${code})` : code;
  } catch {
    return code;
  }
};

/** The common currencies, then every other, alphabetically by name; `current` is always listed. */
export const currencyOptions = (current?: string | null): { common: string[]; others: string[] } => {
  const common: string[] = [...COMMON_CURRENCIES];
  const others = supported().filter((code) => !common.includes(code));
  if (current && isCurrencyCode(current) && !common.includes(current) && !others.includes(current)) {
    others.push(current);
  }
  others.sort((a, b) => currencyLabel(a).localeCompare(currencyLabel(b)));
  return { common, others };
};
