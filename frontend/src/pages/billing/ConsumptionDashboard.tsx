import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, Info, Loader2 } from "lucide-react";

import { getUsageSeries } from "@/services/api/billing";
import { usageKeys } from "@/services/api/queryKeys";

interface Props {
  readonly organizationId: string;
  readonly canManageBilling: boolean;
}

const MICROS = 1_000_000;
const money = (micros: number) => `$${(micros / MICROS).toFixed(2)}`;

export const ConsumptionDashboard: React.FC<Props> = ({
  organizationId,
  canManageBilling,
}) => {
  const [days, setDays] = useState(30);

  const rangeEnd = useMemo(() => new Date(), []);
  const rangeStart = useMemo(
    () => new Date(rangeEnd.getTime() - days * 86_400_000),
    [rangeEnd, days],
  );

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: usageKeys.series(organizationId, rangeStart.toISOString(), "DAY"),
    queryFn: () =>
      getUsageSeries(organizationId, {
        granularity: "DAY",
        from: rangeStart.toISOString(),
        to: rangeEnd.toISOString(),
      }),
    enabled: Boolean(organizationId) && canManageBilling,
    staleTime: 5 * 60_000,
  });

  const byEventType = useMemo(() => {
    const totals = new Map<
      string,
      { quantity: number; cost: number; events: number; unit: string }
    >();
    for (const bucket of data?.buckets ?? []) {
      for (const line of bucket.lines) {
        const existing = totals.get(line.event_type) ?? {
          quantity: 0,
          cost: 0,
          events: 0,
          unit: line.unit,
        };
        existing.quantity += Number(line.quantity);
        existing.cost += line.cost_micros;
        existing.events += line.event_count;
        totals.set(line.event_type, existing);
      }
    }
    return [...totals.entries()].sort((a, b) => b[1].cost - a[1].cost);
  }, [data]);

  const grandTotal = data?.total_cost_micros ?? 0;
  const estimated = data?.estimated_cost_micros ?? 0;
  const sealedShare =
    grandTotal > 0 ? Math.round(((grandTotal - estimated) / grandTotal) * 100) : 100;
  const peak = byEventType[0]?.[1].cost ?? 1;

  if (!canManageBilling) return null;

  if (isLoading) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading consumption…
        </div>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <p role="alert" className="text-sm text-destructive">
          Consumption data couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-border bg-card">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <BarChart3 className="h-4 w-4" />
            Consumption Breakdown
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Usage and billed amounts grouped by event type.
          </p>
        </div>
        <select
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
          aria-label="Time range"
          className="rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </header>

      <div className="space-y-4 p-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-md border border-border bg-background p-3">
            <p className="text-xs text-muted-foreground">Total Charged</p>
            <p className="mt-0.5 text-lg font-semibold text-foreground">{money(grandTotal)}</p>
          </div>
          <div className="rounded-md border border-border bg-background p-3">
            <p className="text-xs text-muted-foreground">Estimated</p>
            <p className="mt-0.5 text-lg font-semibold text-foreground">{money(estimated)}</p>
          </div>
          <div className="rounded-md border border-border bg-background p-3">
            <p className="text-xs text-muted-foreground">Sealed</p>
            <p className="mt-0.5 text-lg font-semibold text-foreground">{sealedShare}%</p>
          </div>
        </div>

        <p className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          Consumption values reflect customer-billed amounts calculated from the active price book.
        </p>

        {byEventType.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No usage recorded in this period.
          </p>
        ) : (
          <ul className="space-y-3">
            {byEventType.map(([eventType, totals]) => (
              <li key={eventType}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-mono text-xs text-foreground font-medium">{eventType}</span>
                  <span className="text-xs text-muted-foreground">
                    {totals.quantity.toLocaleString(undefined, {
                      maximumFractionDigits: 2,
                    })}{" "}
                    {totals.unit} · {totals.events.toLocaleString()} events ·{" "}
                    <strong className="text-foreground font-semibold">
                      {money(totals.cost)}
                    </strong>
                  </span>
                </div>
                <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all duration-300"
                    style={{ width: `${Math.max((totals.cost / peak) * 100, 2)}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};

export default ConsumptionDashboard;
