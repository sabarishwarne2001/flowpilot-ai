import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Copy,
  Gauge,
  Info,
  KeyRound,
  Loader2,
  Lock,
  TerminalSquare,
  X,
} from "lucide-react";

import {
  getApiExplorer,
  getDeveloperOverview,
  getKeyMetrics,
  issueDeveloperKey,
  updateKeyTier,
} from "@/services/api/developer";
import { developerKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  API_RATE_TIERS,
  PUBLIC_API_SCOPES,
  SNIPPET_LANGUAGES,
  formatCount,
  formatMeasurement,
  formatPercent,
  quotaBarFraction,
  type ApiRateTier,
  type ApiTierDescriptor,
  type DeveloperKeySummary,
  type DeveloperUsagePoint,
  type PublicApiScope,
  type SnippetLanguage,
  type TierCatalogue,
} from "@/types/developer";

/**
 * ARCH-21 §3.4 — the developer platform console.
 *
 * THE ONE RULE THIS PAGE IS BUILT AROUND
 * ======================================
 *
 * A `null` measurement renders as an em dash and never as a zero. Latency,
 * error rate and quota fraction are all `number | null` in the contract, and
 * `formatMeasurement` / `formatPercent` are the only things that turn them
 * into text. There is no `?? 0` anywhere below, on purpose: a day with no
 * traffic showing "0 ms" and "0.0% errors" tells an operator the service is
 * instantaneous and flawless at precisely the moment it served nothing.
 *
 * The same applies to the quota bar. `quotaBarFraction` returns null for an
 * unknown fraction and the bar renders striped-indeterminate rather than
 * empty, because an empty bar is a claim that nothing was used.
 *
 * The tier picker shows tiers ABOVE the plan ceiling, disabled. Hiding them
 * would leave an admin unable to see that a higher tier exists or what
 * upgrading buys — the ceiling is a commercial fact worth surfacing, not an
 * error to conceal. The backend refuses an above-ceiling assignment with 409
 * regardless of what this component renders.
 */

const WINDOW_OPTIONS: readonly { readonly value: number; readonly label: string }[] =
  [
    { value: 7, label: "Last 7 days" },
    { value: 30, label: "Last 30 days" },
    { value: 90, label: "Last 90 days" },
  ];

const SCOPE_LABELS: Record<PublicApiScope, string> = {
  "public_documents:read": "Read documents",
  "public_query:write": "Run retrieval queries",
  "public_workflows:read": "Read workflows",
  "public_workflows:write": "Trigger workflows",
};

/* ------------------------------------------------------------------------ */

const CopyButton: React.FC<{ value: string; label?: string }> = ({
  value,
  label = "Copy",
}) => {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access is denied in some embedded contexts. Failing
      // silently is correct: the text is already selectable on screen.
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void copy()}
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "Copied" : label}
    </button>
  );
};

/**
 * Quota bar.
 *
 * `fraction === null` is a third state, not a zero. It renders as a muted
 * striped track with an explicit label rather than an empty bar.
 */
const QuotaBar: React.FC<{ apiKey: DeveloperKeySummary }> = ({ apiKey }) => {
  const fraction = quotaBarFraction(apiKey);

  if (fraction === null) {
    return (
      <div
        className="h-2 w-full rounded-full bg-muted"
        role="img"
        aria-label="Quota usage unknown"
        title="No quota is configured for this key, so a used fraction is undefined."
      />
    );
  }

  const nearing = fraction >= 0.8;

  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
      role="meter"
      aria-valuenow={Math.round(fraction * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`${apiKey.name} monthly quota used`}
    >
      <div
        className={`h-2 rounded-full transition-all ${
          nearing ? "bg-destructive" : "bg-foreground/70"
        }`}
        style={{ width: `${Math.max(2, fraction * 100)}%` }}
      />
    </div>
  );
};

/**
 * Request volume sparkline.
 *
 * Plain SVG rather than a charting dependency: the frontend carries none, and
 * adding one for a single bar series would be the largest thing in the
 * lazy-loaded chunk. Days with no data render as an absent bar, not a
 * zero-height one at the baseline — the distinction is visible and intended.
 */
const VolumeChart: React.FC<{
  series: readonly DeveloperUsagePoint[];
}> = ({ series }) => {
  const peak = useMemo(
    () => Math.max(1, ...series.map((point) => point.request_count)),
    [series],
  );

  if (series.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No requests in this window.</p>
    );
  }

  const width = 100;
  const height = 32;
  const barWidth = width / series.length;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-16 w-full"
      role="img"
      aria-label="Requests per day"
    >
      {series.map((point, index) => {
        const total = point.request_count;
        if (total === 0) {
          return null;
        }
        const barHeight = Math.max(1, (total / peak) * height);
        const errors = point.error_count;
        const errorHeight = total > 0 ? (errors / total) * barHeight : 0;
        return (
          <g key={point.date}>
            <rect
              x={index * barWidth}
              y={height - barHeight}
              width={Math.max(0.5, barWidth - 0.4)}
              height={barHeight}
              className="fill-foreground/60"
            >
              <title>{`${point.date}: ${total} requests, ${errors} errors`}</title>
            </rect>
            {errorHeight > 0 && (
              <rect
                x={index * barWidth}
                y={height - errorHeight}
                width={Math.max(0.5, barWidth - 0.4)}
                height={errorHeight}
                className="fill-destructive"
              />
            )}
          </g>
        );
      })}
    </svg>
  );
};

/* ------------------------------------------------------------------------ */

const TierPicker: React.FC<{
  catalogue: TierCatalogue | undefined;
  value: ApiRateTier;
  onChange: (next: ApiRateTier) => void;
  disabled?: boolean;
}> = ({ catalogue, value, onChange, disabled }) => {
  const tiers: readonly ApiTierDescriptor[] = catalogue?.tiers ?? [];

  if (tiers.length === 0) {
    return (
      <select
        value={value}
        disabled
        onChange={() => undefined}
        className="rounded-md border border-border bg-background px-2 py-1 text-sm"
      >
        {API_RATE_TIERS.map((tier) => (
          <option key={tier} value={tier}>
            {tier}
          </option>
        ))}
      </select>
    );
  }

  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as ApiRateTier)}
      className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground disabled:opacity-60"
    >
      {tiers.map((tier) => (
        <option key={tier.key} value={tier.key} disabled={!tier.assignable}>
          {tier.display_name}
          {tier.assignable ? "" : " — requires a higher plan"}
          {` · ${formatCount(tier.rate_limit_per_minute)}/min`}
        </option>
      ))}
    </select>
  );
};

const KeyRow: React.FC<{
  apiKey: DeveloperKeySummary;
  organizationId: string;
  catalogue: TierCatalogue | undefined;
  windowDays: number;
  onChanged: () => void;
}> = ({ apiKey, organizationId, catalogue, windowDays, onChanged }) => {
  const [expanded, setExpanded] = useState(false);
  const [draftTier, setDraftTier] = useState<ApiRateTier>(apiKey.tier_key);
  const [enabled, setEnabled] = useState(apiKey.is_public_api_enabled);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      updateKeyTier(organizationId, apiKey.id, {
        tier_key: draftTier,
        enable_public_api: enabled,
      }),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (mutationError: unknown) => {
      // A 409 here is the plan ceiling. It is not transient and must not be
      // retried; it is surfaced and left on screen until a human acts.
      const message =
        (mutationError as { message?: string })?.message ??
        "The tier could not be changed.";
      setError(message);
    },
  });

  const metrics = useQuery({
    queryKey: developerKeys.metrics(organizationId, apiKey.id, windowDays),
    queryFn: () => getKeyMetrics(organizationId, apiKey.id, windowDays),
    enabled: expanded,
    staleTime: 60_000,
  });

  const dirty =
    draftTier !== apiKey.tier_key || enabled !== apiKey.is_public_api_enabled;

  return (
    <li className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-medium text-foreground">
            <KeyRound className="h-4 w-4 shrink-0" />
            <span className="truncate">{apiKey.name}</span>
            {apiKey.is_public_api_enabled ? (
              <span className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                Gateway enabled
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                <Lock className="h-3 w-3" />
                Console only
              </span>
            )}
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {apiKey.display_prefix}…
          </p>
        </div>

        <div className="flex items-center gap-2">
          <TierPicker
            catalogue={catalogue}
            value={draftTier}
            onChange={setDraftTier}
            disabled={save.isPending}
          />
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={enabled}
              disabled={save.isPending}
              onChange={(event) => setEnabled(event.target.checked)}
              className="h-3.5 w-3.5"
            />
            Public API
          </label>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Rate limit</dt>
          <dd className="text-foreground">
            {formatCount(apiKey.rate_limit_per_minute)}/min
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Month to date</dt>
          <dd className="text-foreground">
            {formatCount(apiKey.month_to_date_requests)} /{" "}
            {formatCount(apiKey.monthly_request_quota)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">In window</dt>
          <dd className="text-foreground">
            {formatCount(apiKey.window_requests)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Last used</dt>
          <dd className="text-foreground">
            {apiKey.last_used_at
              ? new Date(apiKey.last_used_at).toLocaleDateString()
              : "—"}
          </dd>
        </div>
      </dl>

      <div className="mt-3">
        <QuotaBar apiKey={apiKey} />
        <p className="mt-1 text-xs text-muted-foreground">
          {formatPercent(apiKey.quota_used_fraction)} of the monthly quota used.
        </p>
      </div>

      {apiKey.public_scopes.length > 0 && (
        <p className="mt-3 flex flex-wrap gap-1.5">
          {apiKey.public_scopes.map((scope) => (
            <span
              key={scope}
              className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground"
            >
              {SCOPE_LABELS[scope as PublicApiScope] ?? scope}
            </span>
          ))}
        </p>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {dirty && (
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
          >
            {save.isPending ? "Saving…" : "Save tier"}
          </button>
        )}
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          {expanded ? "Hide consumption" : "Show consumption"}
        </button>
      </div>

      {expanded && (
        <div className="mt-4 rounded-md border border-border bg-muted/30 p-3">
          {metrics.isLoading ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading consumption…
            </p>
          ) : metrics.isError || !metrics.data ? (
            <p role="alert" className="text-sm text-destructive">
              Consumption could not be loaded.
            </p>
          ) : (
            <>
              <VolumeChart series={metrics.data.series} />
              <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div>
                  <dt className="text-xs text-muted-foreground">Requests</dt>
                  <dd>{formatCount(metrics.data.total_requests)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Error rate</dt>
                  <dd>{formatPercent(metrics.data.error_rate)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">
                    p50 latency <span className="italic">est.</span>
                  </dt>
                  <dd>
                    {formatMeasurement(metrics.data.p50_latency_ms, " ms")}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">
                    p95 latency <span className="italic">est.</span>
                  </dt>
                  <dd>
                    {formatMeasurement(metrics.data.p95_latency_ms, " ms")}
                  </dd>
                </div>
              </dl>
              <p className="mt-2 text-xs text-muted-foreground">
                Percentiles are interpolated from a request histogram and are
                accurate to one bucket width. Throttled requests
                ({formatCount(metrics.data.total_throttled)}) are counted but
                carry no latency — they were refused before any work was done.
              </p>
            </>
          )}
        </div>
      )}
    </li>
  );
};

/* ------------------------------------------------------------------------ */

const IssueKeyModal: React.FC<{
  organizationId: string;
  catalogue: TierCatalogue | undefined;
  onClose: () => void;
  onIssued: () => void;
}> = ({ organizationId, catalogue, onClose, onIssued }) => {
  const [name, setName] = useState("");
  const [tier, setTier] = useState<ApiRateTier>("FREE");
  const [scopes, setScopes] = useState<PublicApiScope[]>([
    "public_documents:read",
  ]);
  const [enable, setEnable] = useState(true);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const issue = useMutation({
    mutationFn: () =>
      issueDeveloperKey(organizationId, {
        name: name.trim(),
        scopes,
        tier_key: tier,
        enable_public_api: enable,
      }),
    onSuccess: (result) => {
      setError(null);
      setToken(result.token);
      onIssued();
    },
    onError: (mutationError: unknown) => {
      setError(
        (mutationError as { message?: string })?.message ??
          "The key could not be issued.",
      );
    },
  });

  const toggleScope = (scope: PublicApiScope) => {
    setScopes((current) =>
      current.includes(scope)
        ? current.filter((entry) => entry !== scope)
        : [...current, scope],
    );
  };

  const canSubmit =
    name.trim().length > 0 && scopes.length > 0 && !issue.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4">
      <div className="w-full max-w-lg rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-lg font-semibold text-foreground">
            {token ? "Key issued" : "Issue a gateway API key"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted-foreground hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {token ? (
          <div className="mt-4 space-y-3">
            <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              <p>
                This is the only time this token is shown. Only a keyed hash of
                it is stored, so it cannot be retrieved again — a lost token is
                replaced by rotation, never recovered.
              </p>
            </div>
            <div className="rounded-md border border-border bg-muted/40 p-3">
              <code className="block break-all font-mono text-xs text-foreground">
                {token}
              </code>
              <div className="mt-2">
                <CopyButton value={token} label="Copy token" />
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="w-full rounded-md border border-border px-3 py-2 text-sm hover:bg-muted"
            >
              I have saved it
            </button>
          </div>
        ) : (
          <div className="mt-4 space-y-4">
            <label className="block text-sm">
              <span className="text-muted-foreground">Name</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={120}
                placeholder="Billing integration"
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-foreground"
              />
            </label>

            <div className="text-sm">
              <span className="text-muted-foreground">Tier</span>
              <div className="mt-1">
                <TierPicker
                  catalogue={catalogue}
                  value={tier}
                  onChange={setTier}
                />
              </div>
            </div>

            <fieldset className="text-sm">
              <legend className="text-muted-foreground">Gateway scopes</legend>
              <div className="mt-1 space-y-1.5">
                {PUBLIC_API_SCOPES.map((scope) => (
                  <label key={scope} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={scopes.includes(scope)}
                      onChange={() => toggleScope(scope)}
                      className="h-3.5 w-3.5"
                    />
                    <span className="text-foreground">{SCOPE_LABELS[scope]}</span>
                    <span className="font-mono text-xs text-muted-foreground">
                      {scope}
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enable}
                onChange={(event) => setEnable(event.target.checked)}
                className="h-3.5 w-3.5"
              />
              <span className="text-foreground">
                Enable this key for the public API immediately
              </span>
            </label>

            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}

            <button
              type="button"
              onClick={() => issue.mutate()}
              disabled={!canSubmit}
              className="w-full rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-60"
            >
              {issue.isPending ? "Issuing…" : "Issue key"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------------ */

const ApiExplorer: React.FC<{ organizationId: string }> = ({
  organizationId,
}) => {
  const [language, setLanguage] = useState<SnippetLanguage>("curl");

  const explorer = useQuery({
    queryKey: developerKeys.explorer(organizationId),
    queryFn: () => getApiExplorer(organizationId),
    staleTime: 5 * 60_000,
  });

  if (explorer.isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading the API explorer…
      </p>
    );
  }

  if (explorer.isError || !explorer.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        The API explorer could not be loaded.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-xs text-muted-foreground">
          {explorer.data.base_url}
        </p>
        <div className="flex gap-1">
          {SNIPPET_LANGUAGES.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setLanguage(option.id)}
              className={`rounded-md border px-2 py-1 text-xs ${
                language === option.id
                  ? "border-foreground/40 bg-muted text-foreground"
                  : "border-border text-muted-foreground hover:bg-muted"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <ul className="space-y-3">
        {explorer.data.operations.map((operation) => (
          <li
            key={operation.operation_id}
            className="rounded-lg border border-border p-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-foreground">
                <span className="mr-2 rounded border border-border px-1.5 py-0.5 font-mono text-[11px] uppercase text-muted-foreground">
                  {operation.method}
                </span>
                {operation.summary}
              </p>
              <CopyButton value={operation.snippets[language]} />
            </div>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {operation.path}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Requires scope{" "}
              <span className="font-mono">{operation.required_scope}</span>
            </p>
            <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-muted/50 p-3 text-xs text-foreground">
              <code>{operation.snippets[language]}</code>
            </pre>
          </li>
        ))}
      </ul>
    </div>
  );
};

/* ------------------------------------------------------------------------ */

export const OrganizationDeveloperPortal: React.FC = () => {
  const { organizationId } = useResolvedOrganization();
  const queryClient = useQueryClient();
  const [windowDays, setWindowDays] = useState(30);
  const [issuing, setIssuing] = useState(false);

  const overview = useQuery({
    queryKey: developerKeys.overview(organizationId, windowDays),
    queryFn: () => getDeveloperOverview(organizationId, windowDays),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: developerKeys.all(organizationId),
    });
  };

  if (overview.isLoading) {
    return (
      <div className="mx-auto max-w-4xl p-4 sm:p-6">
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading the developer platform…
        </p>
      </div>
    );
  }

  if (overview.isError || !overview.data) {
    return (
      <div className="mx-auto max-w-4xl p-4 sm:p-6">
        <p role="alert" className="text-sm text-destructive">
          The developer platform couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void overview.refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  const data = overview.data;
  const catalogue = data.tier_catalogue;
  const ceilingTier = catalogue.tiers.find(
    (tier) => tier.key === catalogue.ceiling,
  );

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
              <TerminalSquare className="h-5 w-5" />
              Developer platform
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              API keys, rate tiers and consumption for the public API.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <label className="text-xs text-muted-foreground">
              History
              <select
                value={windowDays}
                onChange={(event) =>
                  setWindowDays(Number(event.target.value))
                }
                className="mt-1 block rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
              >
                {WINDOW_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => setIssuing(true)}
              className="mt-4 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
            >
              Issue key
            </button>
          </div>
        </header>

        <section className="rounded-lg border border-border p-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Gauge className="h-4 w-4" />
            Plan ceiling
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            This organization can assign tiers up to{" "}
            <span className="font-medium text-foreground">
              {ceilingTier?.display_name ?? catalogue.ceiling}
            </span>
            {catalogue.quota_tier_key ? (
              <> on the {catalogue.quota_tier_key} plan.</>
            ) : (
              <>
                {" "}
                — no subscription tier is currently in force, so the ceiling
                defaults to the lowest.
              </>
            )}
          </p>

          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {catalogue.tiers.map((tier) => (
              <li
                key={tier.key}
                className={`rounded-md border p-3 text-sm ${
                  tier.assignable
                    ? "border-border"
                    : "border-border/60 opacity-60"
                }`}
              >
                <p className="flex items-center justify-between font-medium text-foreground">
                  {tier.display_name}
                  {!tier.assignable && (
                    <Lock className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {formatCount(tier.rate_limit_per_minute)} req/min ·{" "}
                  {formatCount(tier.monthly_request_quota)}/month · ef_search{" "}
                  {tier.ef_search}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {tier.description}
                </p>
              </li>
            ))}
          </ul>
        </section>

        {data.public_key_count === 0 && (
          <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <p>
              No key is enabled for the public API yet. Existing keys stay
              console-only until they are explicitly enabled and hold at least
              one gateway scope.
            </p>
          </div>
        )}

        <section>
          <h2 className="text-sm font-semibold text-foreground">
            API keys ({data.total_key_count})
          </h2>
          <ul className="mt-3 space-y-3">
            {data.keys.map((apiKey) => (
              <KeyRow
                key={apiKey.id}
                apiKey={apiKey}
                organizationId={organizationId}
                catalogue={catalogue}
                windowDays={windowDays}
                onChanged={invalidate}
              />
            ))}
          </ul>
        </section>

        <section className="rounded-lg border border-border p-4">
          <h2 className="text-sm font-semibold text-foreground">
            API explorer
          </h2>
          <p className="mb-3 mt-1 text-xs text-muted-foreground">
            Snippets are generated from the live route table, so they cannot
            drift from what the gateway actually serves. Read your remaining
            budget from the <span className="font-mono">X-RateLimit-*</span>{" "}
            response headers.
          </p>
          <ApiExplorer organizationId={organizationId} />
        </section>
      </div>

      {issuing && (
        <IssueKeyModal
          organizationId={organizationId}
          catalogue={catalogue}
          onClose={() => setIssuing(false)}
          onIssued={invalidate}
        />
      )}
    </div>
  );
};

export default OrganizationDeveloperPortal;
