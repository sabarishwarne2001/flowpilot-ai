import type { TokenUsage } from "@/types/assistant";

/**
 * ARCH-39 — what the cost line under an answer says.
 *
 * Before ARCH-39 every answer read "$0.0000", because every TokenUsage was
 * built with a literal zero. The server now fills `estimated_cost` from the
 * price-book settlement and says where the number came from; this helper
 * refuses to print a zero that only means "not priced".
 */
export const formatUsageCost = (usage: TokenUsage): string => {
  switch (usage.cost_source) {
    case "unpriced":
      return "Not priced for this model";
    case "unmetered":
      return "No model call";
    case "price_book":
    case undefined:
    case null:
    default: {
      if (usage.cost_source !== "price_book" && usage.estimated_cost === 0) {
        return "Not available";
      }
      const value = usage.estimated_cost;
      const digits = value > 0 && value < 0.01 ? 6 : 4;
      return `$${value.toFixed(digits)}`;
    }
  }
};
