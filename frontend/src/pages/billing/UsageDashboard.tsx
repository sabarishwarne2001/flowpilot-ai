import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Info, Loader2 } from "lucide-react";

import {
  getUsageLimits,
  getUsageSeries,
  getUsageSummary,
} from "@/services/api/billing";
import { usageKeys } from "@/services/api/queryKeys";
import {
  formatMicros,
  limitUtilisation,
  parseQuantity,
} from "@/types/billing";
import type { UsageGranularity, UsageLimit } from "@/types/billing";

const GRANULARITIES: readonly UsageGranularity[] = ["HOUR", "DAY", "MONTH"];

const daysAgo = (days: number): string => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString();
};

export interface UsageDashboardProps {
  readonly organizationId: string;
}

export const UsageDashboard: React.FC<UsageDashboardProps> = ({
  organizationId,
}) => {
  const [granularity, setGranularity] = useState<UsageGranularity>("DAY");
  const rangeFrom = useMemo(() => daysAgo(30), []);

  const summaryQuery = useQuery({
    queryKey: usageKeys.summary(organizationId, "MONTH"),
    queryFn: () => getUsageSummary(organizationId, { period: "MONTH" }),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const limitsQuery = useQuery({
    queryKey: usageKeys.limits(organizationId),
    queryFn: () => getUsageLimits(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const seriesQuery = useQuery({
    queryKey: usageKeys.series(organizationId, rangeFrom, granularity),
    queryFn: () =>
      getUsageSeries(organizationId, { from: rangeFrom, granularity }),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const summary = summaryQuery.data;
  const currency = summary?.currency ?? "USD";

  const peakBucketCost = useMemo(() => {
    const buckets = seriesQuery.data?.buckets ?? [];
    return buckets.reduce(
      (peak, bucket) => Math.max(peak, bucket.total_cost_micros),
      0,
    );
  }, [seriesQuery.data]);

  if (summaryQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading usage…
      </div>
    );
  }

  if (summaryQuery.isError || !summary) {
    return (
      <div
        role="alert"
        className="m-4 rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive"
      >
        Usage figures couldn&apos;t be loaded.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className="text-sm font-medium text-muted-foreground">
              This period
            </h2>
            <p className="mt-1 text-3xl font-semibold tabular-nums">
              {formatMicros(summary.total_cost_micros, currency)}
            </p>
          </div>

          <div className="text-right text-xs text-muted-foreground">
            <p>
              {new Date(summary.period_start).toLocaleDateString()} –{" "}
              {new Date(summary.period_end).toLocaleDateString()}
            </p>
            <p className="mt-0.5">
              {summary.sealed ? (
                <span className="text-foreground">
                  Final · sealed{" "}
                  {summary.sealed_at
                    ? new Date(summary.sealed_at).toLocaleDateString()
                    : ""}
                </span>
              ) : (
                <span>Running total — not yet final</span>
              )}
            </p>
          </div>
        </div>

        <div className="mt-3 space-y-1.5">
          {summary.estimated_cost_micros > 0 && (
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>
                {formatMicros(summary.estimated_cost_micros, currency)} of this
                is estimated — the provider returned no token counts for some
                generations.
              </span>
            </p>
          )}

          {summary.late_cost_micros > 0 && (
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>
                {formatMicros(summary.late_cost_micros, currency)} arrived after
                the previous period closed and is attributed here.
              </span>
            </p>
          )}
        </div>
      </section>

      <section>
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-sm font-medium">Limits</h2>
          {limitsQuery.data?.quota_tier_display_name && (
            <p className="text-xs text-muted-foreground">
              {limitsQuery.data.quota_tier_display_name}
              {limitsQuery.data.quota_tier_version !== null &&
                ` · v${limitsQuery.data.quota_tier_version}`}
            </p>
          )}
        </div>

        {limitsQuery.isLoading ? (
          <p className="mt-2 text-sm text-muted-foreground">Loading limits…</p>
        ) : (limitsQuery.data?.limits.length ?? 0) === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">
            No limits are configured for this organization.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {limitsQuery.data?.limits.map((limit) => (
              <LimitRow
                key={`${limit.limit_key}:${limit.period}`}
                limit={limit}
                currency={currency}
              />
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium">Last 30 days</h2>
          <div
            className="flex items-center gap-1"
            role="group"
            aria-label="Granularity"
          >
            {GRANULARITIES.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setGranularity(option)}
                aria-pressed={granularity === option}
                className={[
                  "rounded border px-2 py-0.5 text-xs",
                  granularity === option
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:bg-muted",
                ].join(" ")}
              >
                {option.charAt(0) + option.slice(1).toLowerCase()}
              </button>
            ))}
          </div>
        </div>

        {seriesQuery.isLoading ? (
          <p className="mt-2 text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div
            className="mt-3 flex h-32 items-end gap-[2px]"
            role="img"
            aria-label={`Usage by ${granularity.toLowerCase()} over the last 30 days`}
          >
            {(seriesQuery.data?.buckets ?? []).map((bucket) => {
              const height =
                peakBucketCost > 0
                  ? (bucket.total_cost_micros / peakBucketCost) * 100
                  : 0;

              return (
                <div
                  key={bucket.bucket_start}
                  title={`${new Date(bucket.bucket_start).toLocaleString()} — ${formatMicros(bucket.total_cost_micros, currency)}${bucket.sealed ? "" : " (running)"}`}
                  className={[
                    "min-w-[3px] flex-1 rounded-t transition-colors",
                    bucket.sealed ? "bg-primary/70" : "bg-primary/30",
                  ].join(" ")}
                  style={{ height: `${Math.max(height, 1)}%` }}
                />
              );
            })}
          </div>
        )}

        <p className="mt-2 text-xs text-muted-foreground">
          Lighter bars are periods that have not sealed yet and can still move.
        </p>
      </section>

      {summary.lines.length > 0 && (
        <section>
          <h2 className="text-sm font-medium">Breakdown</h2>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th scope="col" className="py-2 pr-4 font-medium">
                    Event
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    Quantity
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    Events
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    Cost
                  </th>
                </tr>
              </thead>
              <tbody>
                {summary.lines.map((line) => (
                  <tr key={line.event_type} className="border-b border-border/50">
                    <td className="py-2 pr-4">{line.event_type}</td>
                    <td className="py-2 pr-4 tabular-nums">
                      {parseQuantity(line.quantity).toLocaleString()}{" "}
                      <span className="text-xs text-muted-foreground">
                        {line.unit}
                      </span>
                    </td>
                    <td className="py-2 pr-4 tabular-nums text-muted-foreground">
                      {line.event_count.toLocaleString()}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {formatMicros(line.cost_micros, currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
};

interface LimitRowProps {
  readonly limit: UsageLimit;
  readonly currency: string;
}

const LimitRow: React.FC<LimitRowProps> = ({ limit, currency }) => {
  const utilisation = limitUtilisation(limit);
  const pct = utilisation === null ? null : Math.min(utilisation * 100, 100);
  const over = utilisation !== null && utilisation >= 1;
  const near = utilisation !== null && utilisation >= 0.8 && !over;

  return (
    <li className="rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{limit.limit_key}</span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {limit.max_cost_micros !== null
            ? `${formatMicros(limit.current_cost_micros, currency)} of ${formatMicros(limit.max_cost_micros, currency)}`
            : limit.max_quantity !== null
              ? `${parseQuantity(limit.current_quantity).toLocaleString()} of ${parseQuantity(limit.max_quantity).toLocaleString()}`
              : "No ceiling"}
        </span>
      </div>

      {pct !== null && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={[
              "h-full transition-[width] duration-300",
              over
                ? "bg-destructive"
                : near
                  ? "bg-amber-500"
                  : "bg-primary",
            ].join(" ")}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>Resets {new Date(limit.resets_at).toLocaleDateString()}</span>
        {limit.hard_stop ? (
          <span className="inline-flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" aria-hidden="true" />
            Hard stop — requests are refused past this
          </span>
        ) : (
          <span>Overage: {limit.overage_policy.toLowerCase()}</span>
        )}
      </div>

      {over && (
        <p className="mt-1.5 text-xs font-medium text-destructive">
          {limit.hard_stop
            ? "Limit reached. Requests against this limit are being refused."
            : "Over the included amount. Overage applies."}
        </p>
      )}
    </li>
  );
};

export default UsageDashboard;
