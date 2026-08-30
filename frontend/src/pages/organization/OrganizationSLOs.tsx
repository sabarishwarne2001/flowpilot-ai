import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Gauge,
  Info,
  Loader2,
  RotateCcw,
} from "lucide-react";

import {
  clearOrganizationSLO,
  getOrganizationSLOs,
  setOrganizationSLO,
} from "@/services/api/slos";
import { sloKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  formatSLOValue,
  sloGaugeFraction,
  type SLOComplianceEntry,
  type SLOWindow,
} from "@/types/slo";

const PERIODS: readonly { readonly value: SLOWindow; readonly label: string }[] = [
  { value: "HOUR", label: "Last 24 hours" },
  { value: "DAY", label: "Last 30 days" },
  { value: "MONTH", label: "Last 12 months" },
];

const GaugeBar: React.FC<{ entry: SLOComplianceEntry }> = ({ entry }) => {
  const fraction = sloGaugeFraction(entry);

  if (fraction === null) {
    return (
      <div className="h-2 w-full rounded-full bg-muted">
        <div className="h-2 w-0 rounded-full" />
      </div>
    );
  }

  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
      role="meter"
      aria-valuenow={Math.round(fraction * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`${entry.target.display_name} against target`}
    >
      <div
        className={`h-2 rounded-full transition-all ${
          entry.breached ? "bg-destructive" : "bg-foreground/70"
        }`}
        style={{ width: `${Math.max(2, fraction * 100)}%` }}
      />
    </div>
  );
};

const SourceBadge: React.FC<{ entry: SLOComplianceEntry }> = ({ entry }) => {
  const { source, is_contractual: isContractual } = entry.target;

  const label =
    source === "ORGANIZATION"
      ? "Custom target"
      : source === "PLATFORM_DEFAULT"
        ? "Platform default"
        : "Platform default (unconfigured)";

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
        {label}
      </span>
      {isContractual ? (
        <span className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium text-foreground">
          Contractual
        </span>
      ) : null}
    </span>
  );
};

const SLORow: React.FC<{
  entry: SLOComplianceEntry;
  organizationId: string;
  onChanged: () => void;
}> = ({ entry, organizationId, onChanged }) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.target.target_value);
  const [contractual, setContractual] = useState(entry.target.is_contractual);

  const save = useMutation({
    mutationFn: () =>
      setOrganizationSLO(organizationId, entry.slo_key, {
        target_value: draft,
        is_contractual: contractual,
      }),
    onSuccess: () => {
      setEditing(false);
      onChanged();
    },
  });

  const reset = useMutation({
    mutationFn: () => clearOrganizationSLO(organizationId, entry.slo_key),
    onSuccess: () => {
      setEditing(false);
      onChanged();
    },
  });

  const hasSamples = entry.sample_count > 0;
  const isEstimate = entry.method === "HISTOGRAM_INTERPOLATED";

  return (
    <li className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {entry.breached ? (
              <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
            ) : (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <h3 className="truncate text-sm font-medium text-foreground">
              {entry.target.display_name}
            </h3>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {entry.target.description}
          </p>
        </div>

        <div className="text-right">
          <p className="text-lg font-semibold tabular-nums text-foreground">
            {formatSLOValue(entry.observed_value, entry.target.unit)}
            {hasSamples && isEstimate ? (
              <span
                className="ml-1 align-super text-[10px] font-normal text-muted-foreground"
                title="Estimated from a latency histogram, accurate to one bucket width. The breach verdict beside it is exact."
              >
                est.
              </span>
            ) : null}
          </p>
          <p className="text-xs text-muted-foreground">
            target {formatSLOValue(entry.target.target_value, entry.target.unit)}
          </p>
        </div>
      </div>

      <div className="mt-3">
        <GaugeBar entry={entry} />
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <SourceBadge entry={entry} />
        <span className="tabular-nums">
          {hasSamples
            ? `${entry.sample_count.toLocaleString()} samples this ${entry.target.window_period.toLowerCase()}`
            : "No traffic in this window"}
          {entry.total_windows > 0
            ? ` · ${entry.total_windows - entry.breached_windows}/${entry.total_windows} windows met`
            : " · no sealed history yet"}
        </span>
      </div>

      {editing ? (
        <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-border pt-3">
          <label className="text-xs text-muted-foreground">
            Target
            <input
              type="text"
              inputMode="decimal"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="mt-1 block w-32 rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
              aria-label={`Target for ${entry.target.display_name}`}
            />
            <span className="mt-1 block text-[11px]">
              {entry.target.unit === "RATIO"
                ? "A proportion between 0 and 1 — 99.9% is 0.999"
                : "Milliseconds"}
            </span>
          </label>

          <label className="flex items-center gap-2 pb-1 text-xs text-foreground">
            <input
              type="checkbox"
              checked={contractual}
              onChange={(event) => setContractual(event.target.checked)}
            />
            Contractual
          </label>

          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted"
          >
            Cancel
          </button>

          {entry.target.source === "ORGANIZATION" ? (
            <button
              type="button"
              onClick={() => reset.mutate()}
              disabled={reset.isPending}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted disabled:opacity-50"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Use platform default
            </button>
          ) : null}

          {save.isError ? (
            <p role="alert" className="w-full text-xs text-destructive">
              That target was rejected. Ratios must be between 0 and 1.
            </p>
          ) : null}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => {
            setDraft(entry.target.target_value);
            setContractual(entry.target.is_contractual);
            setEditing(true);
          }}
          className="mt-3 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          Change target
        </button>
      )}
    </li>
  );
};

export const OrganizationSLOs: React.FC = () => {
  const { organizationId } = useResolvedOrganization();
  const [period, setPeriod] = useState<SLOWindow>("DAY");
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: sloKeys.summary(organizationId, period),
    queryFn: () => getOrganizationSLOs(organizationId, period),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: sloKeys.all(organizationId) });
  };

  const entries = useMemo(() => data?.entries ?? [], [data]);
  const breaches = data?.contractual_breaches ?? 0;
  const unmeasured = entries.filter((entry) => entry.sample_count === 0).length;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl p-4 sm:p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading service levels…
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto max-w-4xl p-4 sm:p-6">
        <p role="alert" className="text-sm text-destructive">
          Service levels couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
              <Gauge className="h-5 w-5" />
              Service levels
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Live compliance for this organization, measured per tenant.
            </p>
          </div>

          <label className="text-xs text-muted-foreground">
            History
            <select
              value={period}
              onChange={(event) => setPeriod(event.target.value as SLOWindow)}
              className="mt-1 block rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
            >
              {PERIODS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </header>

        {breaches > 0 ? (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-foreground">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <p>
              {breaches} contractual {breaches === 1 ? "target is" : "targets are"}{" "}
              currently in breach for the open window.
            </p>
          </div>
        ) : null}

        {unmeasured > 0 ? (
          <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <p>
              {unmeasured} of {entries.length} targets have no samples in the
              current window. They are shown as unmeasured, not as met.
            </p>
          </div>
        ) : null}

        <ul className="space-y-3">
          {entries.map((entry) => (
            <SLORow
              key={entry.slo_key}
              entry={entry}
              organizationId={organizationId}
              onChanged={invalidate}
            />
          ))}
        </ul>

        <p className="text-xs text-muted-foreground">
          Latency figures marked <span className="font-medium">est.</span> are
          interpolated from a request histogram and are accurate to one bucket
          width. Breach verdicts for contractual targets are computed exactly
          against a bucket boundary placed at the target itself.
          {isFetching ? " Refreshing…" : ""}
        </p>
      </div>
    </div>
  );
};

export default OrganizationSLOs;
