/**
 * ARCH-29 Tranche 2 — human-readable plan entitlements.
 *
 * THE PROBLEM
 * ===========
 *
 * `PlanSelector` rendered `e.event_type` raw, in a monospace span, straight
 * from the database:
 *
 *     llm.input_token: 2,000,000 per month
 *     storage.gb_month: 25 per month
 *     ocr.page: 50,000 per month
 *     *: Unlimited
 *
 * That is a schema key on a pricing page. The `*` row is worse than untidy —
 * it is the catch-all entry, and rendering it as a bullet reading
 * "*: Unlimited" above three explicit limits reads as though the plan is
 * unlimited and then contradicts itself three times.
 *
 * WHY A TYPED RECORD AND NOT A LOOKUP WITH A STRING FALLBACK
 * ==========================================================
 *
 * The obvious shape is `LABELS[key] ?? key`, which never fails and therefore
 * never tells you it is failing. A meter added in ARCH-30 would render as
 * `llm.embedding_token` on the pricing page of a live product, and nothing
 * would break, no test would go red, and the first report would come from a
 * customer.
 *
 * `KNOWN_METERS` is keyed by a union type, so adding a member to `MeterKey`
 * without adding its label is a COMPILE ERROR, and `verify_arch29_tranche2.py`
 * G6 asserts the union covers every `limit_key` the seed script publishes. The
 * runtime fallback still exists — a database can hold a key this build has
 * never heard of — but it renders as a plain-English "additional allowance"
 * line rather than leaking the key, and it is a state the type system has
 * already made hard to reach.
 *
 * WHY UNITS ARE FORMATTED PER METER
 * =================================
 *
 * "2,000,000 per month" is technically accurate and commercially useless.
 * Customers cannot forecast tokens, which is why the blueprint bills credits.
 * Tokens render as "2M AI tokens", storage as "25 GB", pages as "50,000 pages"
 * — the same number, in the unit the buyer thinks in.
 */

export type MeterKey =
  | "addon.custom_domain"
  | "addon.warehouse_sync"
  | "*"
  | "llm.input_token"
  | "llm.output_token"
  | "llm.platform_key"
  | "ocr.page"
  | "storage.gb_month"
  | "api.request"
  | "embedding.token";

interface MeterDisplay {
  /** Noun phrase, sentence case, no leading count. */
  readonly label: string;
  /** How the quantity is rendered. */
  readonly unit: "tokens" | "pages" | "gigabytes" | "requests" | "count" | "none";
  /**
   * Meters that describe a capability rather than a quantity. Their presence
   * IS the grant, so a count would be meaningless.
   */
  readonly capability?: boolean;
  /**
   * Hidden from plan cards. The catch-all entry is a policy default, not a
   * feature, and listing it misrepresents the explicit limits beside it.
   */
  readonly hidden?: boolean;
}

export const KNOWN_METERS: Record<MeterKey, MeterDisplay> = {
  "*": {
    label: "All other usage",
    unit: "none",
    hidden: true,
  },
  "llm.input_token": {
    label: "AI tokens in",
    unit: "tokens",
  },
  "llm.output_token": {
    label: "AI tokens out",
    unit: "tokens",
  },
  "llm.platform_key": {
    // ARCH-29 D-2. Its presence entitles the tenant to inference on the
    // platform's provider account; withheld, they must bring their own key.
    label: "AI included (no provider key needed)",
    unit: "none",
    capability: true,
  },
  "ocr.page": {
    label: "Document pages processed",
    unit: "pages",
  },
  "storage.gb_month": {
    label: "Document storage",
    unit: "gigabytes",
  },
  "api.request": {
    label: "API requests",
    unit: "requests",
  },
  "embedding.token": {
    label: "Search indexing",
    unit: "tokens",
  },
  "addon.custom_domain": {
    label: "Custom vanity domain",
    unit: "none",
    capability: true,
  },
  "addon.warehouse_sync": {
    label: "Data warehouse sync",
    unit: "none",
    capability: true,
  },
};

const PERIOD_LABELS: Record<string, string> = {
  DAY: "day",
  WEEK: "week",
  MONTH: "month",
  YEAR: "year",
};

/**
 * 2000000 → "2M", 50000 → "50,000".
 *
 * Only tokens abbreviate. "50K pages" reads as an approximation, and a page
 * count is exact — the customer is buying that many, not about that many.
 */
function formatTokenCount(value: number): string {
  if (value >= 1_000_000 && value % 1_000_000 === 0) {
    return `${value / 1_000_000}M`;
  }
  if (value >= 1_000 && value % 1_000 === 0) {
    return `${value / 1_000}K`;
  }
  return value.toLocaleString();
}

function formatQuantity(unit: MeterDisplay["unit"], value: number): string {
  switch (unit) {
    case "tokens":
      return formatTokenCount(value);
    case "gigabytes":
      return `${value.toLocaleString()} GB`;
    case "pages":
    case "requests":
    case "count":
      return value.toLocaleString();
    case "none":
      return "";
  }
}

export interface EntitlementLine {
  readonly key: string;
  readonly text: string;
}

/**
 * Render one entitlement, or null when it should not appear on a plan card.
 *
 * `limitQuantity === null` means no ceiling. It is rendered as "Unlimited"
 * rather than omitted, because an absent line and an uncapped line are
 * different promises.
 */
export function describeEntitlement(
  eventType: string,
  limitQuantity: number | null,
  period: string,
): EntitlementLine | null {
  const known = (KNOWN_METERS as Record<string, MeterDisplay | undefined>)[
    eventType
  ];

  if (known?.hidden) {
    return null;
  }

  const periodLabel = PERIOD_LABELS[period.toUpperCase()] ?? period.toLowerCase();

  if (known === undefined) {
    // A meter this build does not know. Say something true and generic rather
    // than printing the schema key at a prospective customer.
    return {
      key: eventType,
      text:
        limitQuantity === null
          ? "Additional allowance included"
          : `${limitQuantity.toLocaleString()} additional units per ${periodLabel}`,
    };
  }

  if (known.capability) {
    return { key: eventType, text: known.label };
  }

  if (limitQuantity === null) {
    return { key: eventType, text: `Unlimited ${known.label.toLowerCase()}` };
  }

  const quantity = formatQuantity(known.unit, limitQuantity);
  return {
    key: eventType,
    text: `${quantity} ${known.label.toLowerCase()} / ${periodLabel}`,
  };
}
