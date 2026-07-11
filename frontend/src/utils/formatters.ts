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

/**
 * Format dates using the user's locale.
 */
export const formatDateTime = (
  dateInput: string | number | Date,
): string => {
  try {
    const date = new Date(dateInput);

    if (Number.isNaN(date.getTime())) {
      return "Invalid Date";
    }

    return new Intl.DateTimeFormat(
      navigator.language,
      {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      },
    ).format(date);
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
