/**
 * Global formatting utilities for FlowPilot AI.
 *
 * Contains pure, reusable helper functions for presenting
 * data consistently across the application.
 */

const FILE_SIZE_UNITS = [
  "Bytes",
  "KB",
  "MB",
  "GB",
  "TB",
  "PB",
] as const;

/**
 * Convert bytes into a human-readable string.
 */
export const formatBytes = (
  bytes: number,
  decimals = 2,
): string => {
  if (
    !Number.isFinite(bytes) ||
    bytes <= 0
  ) {
    return "0 Bytes";
  }

  const unit = 1024;
  const precision = Math.max(0, decimals);

  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(unit)),
    FILE_SIZE_UNITS.length - 1,
  );

  const value = bytes / Math.pow(unit, index);

  return `${Number(value.toFixed(precision))} ${FILE_SIZE_UNITS[index]}`;
};

import { formatTimestamp } from "@/utils/displayTime";

/**
 * Format dates in the reader's profile timezone and language (ARCH-30 D-5).
 */
export const formatDateTime = (
  dateInput: string | number | Date,
): string => {
  try {
    const date = new Date(dateInput);

    if (Number.isNaN(date.getTime())) {
      return "Invalid Date";
    }

    return formatTimestamp(date, "Invalid Date");
  } catch {
    return "Invalid Date";
  }
};

/**
 * Format currency.
 */
export const formatCurrency = (
  amount: number,
  currency = "USD",
): string => {
  try {
    return new Intl.NumberFormat(
      navigator.language,
      {
        style: "currency",
        currency: currency.toUpperCase(),
      },
    ).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency.toUpperCase()}`;
  }
};

/**
 * Truncate long text.
 */
export const truncateText = (
  text: string,
  maxLength: number,
): string => {
  if (!text) {
    return "";
  }

  if (text.length <= maxLength) {
    return text;
  }

  return `${text.slice(0, maxLength).trim()}...`;
};

/**
 * Format relative time.
 *
 * Example:
 * - Just now
 * - 5 minutes ago
 * - Yesterday
 */
export const formatRelativeTime = (
  dateInput: string | number | Date,
): string => {
  const date = new Date(dateInput);

  if (Number.isNaN(date.getTime())) {
    return "Invalid Date";
  }

  const seconds = Math.floor(
    (Date.now() - date.getTime()) / 1000,
  );

  const formatter = new Intl.RelativeTimeFormat(
    navigator.language,
    {
      numeric: "auto",
    },
  );

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

  const years = Math.floor(months / 12);

  return formatter.format(-years, "year");
};

/**
 * Capitalize the first letter.
 */
export const capitalize = (
  value: string,
): string => {
  if (!value) {
    return "";
  }

  return (
    value.charAt(0).toUpperCase() +
    value.slice(1)
  );
};

/**
 * An amount held in micros (millionths), in the currency the document states.
 * Without a currency it is a plain grouped number: printing a guessed symbol
 * (F-172 printed every variance in rupees) is worse than printing none.
 */
export const formatMoneyMicros = (
  micros: number | null | undefined,
  currency: string | null | undefined,
  options: { signed?: boolean } = {},
): string => {
  if (micros === null || micros === undefined || !Number.isFinite(micros)) {
    return "—";
  }
  const amount = micros / 1_000_000;
  const signDisplay = options.signed ? "exceptZero" : "auto";
  if (currency && /^[A-Za-z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: currency.toUpperCase(),
        maximumFractionDigits: 2,
        signDisplay,
      }).format(amount);
    } catch {
      // an ISO-shaped code the browser does not know: fall through to a plain number
    }
  }
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay,
  }).format(amount);
};

/**
 * F-174. The vendor a person reads: the name the document prints, else the
 * matching key made readable ("name:acme industrial supplies" is an internal
 * normalised key, never a label).
 */
export const vendorLabel = (
  name: string | null | undefined,
  key: string | null | undefined,
): string => {
  if (name && name.trim()) {
    return name.trim();
  }
  if (!key) {
    return "Unknown vendor";
  }
  const [kind, ...rest] = key.split(":");
  const value = rest.join(":").trim();
  if (!value) {
    return key;
  }
  if (kind === "tax") {
    return `Tax ID ${value.toUpperCase()}`;
  }
  return value.replace(/\b\p{L}/gu, (letter) => letter.toUpperCase());
};
