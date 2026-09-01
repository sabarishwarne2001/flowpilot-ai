/**
 * ARCH-21 — developer platform contracts.
 *
 * Mirrors app/schemas/developer.py and app/schemas/public_api.py.
 *
 * EVERY LATENCY AND RATE FIELD IS `number | null`, NOT `number`.
 * ==============================================================
 *
 * `null` means "no measurement", and the components in
 * OrganizationDeveloperPortal.tsx render it as an em dash. Typing these as
 * `number` would push the backend's honest null through `?? 0` at some call
 * site, and a window with no traffic would render as a service that responds
 * in zero milliseconds with a flawless error rate. That is the same
 * laundering of an unknown into a flattering number that ARCH-18 prohibited
 * when it banned COALESCE(cost_basis_micros, 0), and the type is what stops
 * it here.
 */

/** Mirrors app/core/api_tiers.py::ApiRateTier. */
export const API_RATE_TIERS = ["FREE", "BUILDER", "PRO", "ENTERPRISE"] as const;

export type ApiRateTier = (typeof API_RATE_TIERS)[number];

/** Mirrors app/core/scopes.py::PUBLIC_API_SCOPES. */
export const PUBLIC_API_SCOPES = [
  "public_documents:read",
  "public_query:write",
  "public_workflows:read",
  "public_workflows:write",
] as const;

export type PublicApiScope = (typeof PUBLIC_API_SCOPES)[number];

export interface ApiTierDescriptor {
  readonly key: ApiRateTier;
  readonly display_name: string;
  readonly rank: number;
  readonly rate_limit_per_minute: number;
  readonly monthly_request_quota: number;
  readonly ef_search: number;
  readonly description: string;
  /**
   * False when the tier is above the organization's plan ceiling. The tier is
   * still listed — hiding it would leave an admin unable to see that a higher
   * tier exists or what upgrading buys.
   */
  readonly assignable: boolean;
}

export interface TierCatalogue {
  readonly ceiling: ApiRateTier;
  /** null when no quota_tiers version is in force. Resolves the ceiling to FREE. */
  readonly quota_tier_key: string | null;
  readonly tiers: readonly ApiTierDescriptor[];
}

export interface DeveloperKeySummary {
  readonly id: string;
  readonly name: string;
  /** Computed from the key id. There is no prefix column; this carries no secret. */
  readonly display_prefix: string;
  readonly tier_key: ApiRateTier;
  readonly rate_limit_per_minute: number;
  readonly monthly_request_quota: number;
  readonly is_public_api_enabled: boolean;
  readonly scopes: readonly string[];
  readonly public_scopes: readonly string[];
  readonly expires_at: string | null;
  readonly last_used_at: string | null;
  readonly month_to_date_requests: number;
  readonly window_requests: number;
  /** null when the quota is zero — a fraction of nothing is undefined, not 0. */
  readonly quota_used_fraction: number | null;
  readonly created_at: string;
}

export interface DeveloperOverview {
  readonly organization_id: string;
  readonly window_days: number;
  readonly window_start: string;
  readonly window_end: string;
  readonly tier_catalogue: TierCatalogue;
  readonly keys: readonly DeveloperKeySummary[];
  readonly public_key_count: number;
  readonly total_key_count: number;
  readonly month_to_date_requests: number;
}

export interface DeveloperUsagePoint {
  readonly date: string;
  readonly request_count: number;
  readonly error_count: number;
  readonly throttled_count: number;
  readonly mean_latency_ms: number | null;
}

export interface DeveloperKeyMetrics {
  readonly api_key_id: string;
  readonly window_days: number;
  readonly window_start: string;
  readonly window_end: string;
  readonly total_requests: number;
  readonly total_errors: number;
  readonly total_throttled: number;
  readonly served_requests: number;
  readonly error_rate: number | null;
  readonly mean_latency_ms: number | null;
  readonly p50_latency_ms: number | null;
  readonly p95_latency_ms: number | null;
  /** Always HISTOGRAM_INTERPOLATED. Percentiles are estimates to one bucket width. */
  readonly latency_method: string;
  readonly month_to_date_requests: number;
  readonly series: readonly DeveloperUsagePoint[];
}

export interface DeveloperTierUpdateRequest {
  readonly tier_key: ApiRateTier;
  readonly enable_public_api?: boolean;
}

export interface DeveloperKeyCreateRequest {
  readonly name: string;
  readonly scopes: readonly string[];
  readonly tier_key: ApiRateTier;
  readonly expires_at?: string | null;
  readonly enable_public_api: boolean;
}

export interface DeveloperKeyIssued {
  readonly api_key: DeveloperKeySummary;
  /** Shown once. Never stored, never logged, never re-fetchable. */
  readonly token: string;
}

export interface CodeSnippetSet {
  readonly curl: string;
  readonly python: string;
  readonly typescript: string;
}

export type SnippetLanguage = keyof CodeSnippetSet;

export const SNIPPET_LANGUAGES: readonly {
  readonly id: SnippetLanguage;
  readonly label: string;
}[] = [
  { id: "curl", label: "cURL" },
  { id: "python", label: "Python" },
  { id: "typescript", label: "TypeScript" },
];

export interface ApiExplorerOperation {
  readonly operation_id: string;
  readonly method: string;
  readonly path: string;
  readonly summary: string;
  readonly required_scope: string;
  readonly snippets: CodeSnippetSet;
}

export interface ApiExplorerCatalogue {
  readonly base_url: string;
  readonly operations: readonly ApiExplorerOperation[];
}

/* ------------------------------------------------------------------------ */
/* Formatting helpers                                                        */
/* ------------------------------------------------------------------------ */

/**
 * The one place a null becomes visible text.
 *
 * Centralised so no component reaches for `?? 0`. If a number is absent the
 * user sees an em dash and the tooltip explains why; they never see a zero
 * that was never measured.
 */
export const formatMeasurement = (
  value: number | null | undefined,
  unit: string,
  fractionDigits = 0,
): string => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(fractionDigits)}${unit}`;
};

export const formatPercent = (
  fraction: number | null | undefined,
  fractionDigits = 1,
): string => {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) {
    return "—";
  }
  return `${(fraction * 100).toFixed(fractionDigits)}%`;
};

export const formatCount = (value: number): string =>
  new Intl.NumberFormat(undefined, { notation: "compact" }).format(value);

/**
 * Quota bar width. Returns null rather than 0 when the fraction is unknown,
 * so the caller renders an indeterminate bar instead of an empty one — an
 * empty bar reads as "nothing used", which is a claim.
 */
export const quotaBarFraction = (
  key: Pick<DeveloperKeySummary, "quota_used_fraction">,
): number | null => {
  const value = key.quota_used_fraction;
  if (value === null || value === undefined || Number.isNaN(value)) {
    return null;
  }
  return Math.max(0, Math.min(1, value));
};

export const isTierAssignable = (
  catalogue: TierCatalogue | undefined,
  tier: ApiRateTier,
): boolean =>
  catalogue?.tiers.find((entry) => entry.key === tier)?.assignable ?? false;
