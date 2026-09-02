import React, { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Info, Loader2, ShieldAlert } from "lucide-react";

import { getUsageLimits, setSpendLimit } from "@/services/api/billing";
import { usageKeys } from "@/services/api/queryKeys";
import { SPEND_LIMIT_KEYS } from "@/types/usage";
import type { SpendLimit, SpendLimitPeriod } from "@/types/usage";
import type { UsageLimit } from "@/types/billing";

interface Props {
  readonly organizationId: string;
  readonly canManageBilling: boolean;
}

const KEY_LABEL: Readonly<Record<string, string>> = {
  "*": "Total spend (all usage)",
  "ocr.page": "OCR pages",
  "embedding.token": "Embedding tokens",
  "llm.input_token": "LLM input tokens",
  "llm.output_token": "LLM output tokens",
  "storage.gb_month": "Storage (GB-months)",
  "document.processed": "Documents processed",
};

const MICROS_PER_UNIT = 1_000_000;

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {return detail;}
  if (Array.isArray(detail) && detail[0]?.msg) {return String(detail[0].msg);}
  return fallback;
}

export const SpendLimitForm: React.FC<Props> = ({
  organizationId,
  canManageBilling,
}) => {
  const [limitKey, setLimitKey] = useState<string>("*");
  const [period, setPeriod] = useState<SpendLimitPeriod>("MONTH");
  const [maxQuantity, setMaxQuantity] = useState("");
  const [maxCost, setMaxCost] = useState("");
  const [hardStop, setHardStop] = useState(true);
  const [note, setNote] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [sessionLimits, setSessionLimits] = useState<SpendLimit[]>([]);

  const { data: effective } = useQuery({
    queryKey: usageKeys.limits(organizationId),
    queryFn: () => getUsageLimits(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const currentEffective = useMemo(
    () =>
      effective?.limits?.find(
        (limit: UsageLimit) => limit.limit_key === limitKey,
      ),
    [effective, limitKey],
  );

  const costMicros = useMemo(() => {
    const parsed = Number.parseFloat(maxCost);
    if (Number.isNaN(parsed) || parsed < 0) {return null;}
    return Math.round(parsed * MICROS_PER_UNIT);
  }, [maxCost]);

  const quantityValue = maxQuantity.trim() === "" ? null : maxQuantity.trim();
  const hasCeiling = quantityValue !== null || costMicros !== null;

  const save = useMutation({
    mutationFn: () =>
      setSpendLimit(organizationId, {
        limit_key: limitKey,
        period,
        max_quantity: quantityValue,
        max_cost_micros: costMicros,
        hard_stop: hardStop,
        note: note.trim() || null,
      }),
    onSuccess: (limit) => {
      setError(null);
      setSessionLimits((current) => [
        limit,
        ...current.filter(
          (existing) =>
            !(existing.limit_key === limit.limit_key &&
              existing.period === limit.period),
        ),
      ]);
      setMaxQuantity("");
      setMaxCost("");
      setNote("");
    },
    onError: (err) =>
      setError(
        detailOf(err, "That limit couldn't be saved. Please check the values."),
      ),
  });

  if (!canManageBilling) {return null;}

  return (
    <section className="rounded-lg border border-border bg-card">
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Spend limits</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Caps how much this organization can consume in a period. Applied on
          top of your plan&apos;s built-in quotas.
        </p>
      </header>

      <div className="space-y-4 p-4">
        <p className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          Saving replaces any existing limit for the same measure and period.
          Configured limits can&apos;t be listed back yet, so note what you set.
        </p>

        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {error}
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="limit-key" className="text-sm font-medium text-foreground">
              Measure
            </label>
            <select
              id="limit-key"
              value={limitKey}
              onChange={(event) => setLimitKey(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
            >
              {SPEND_LIMIT_KEYS.map((key) => (
                <option key={key} value={key}>
                  {KEY_LABEL[key] ?? key}
                </option>
              ))}
            </select>
            {currentEffective && (
              <p className="mt-1 text-xs text-muted-foreground">
                Currently in force:{" "}
                {currentEffective.max_quantity === null
                  ? "unlimited"
                  : Number(currentEffective.max_quantity).toLocaleString()}
                {" — includes your plan's default, which this would override."}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="limit-period" className="text-sm font-medium text-foreground">
              Period
            </label>
            <select
              id="limit-period"
              value={period}
              onChange={(event) =>
                setPeriod(event.target.value as SpendLimitPeriod)
              }
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
            >
              <option value="DAY">Per day</option>
              <option value="MONTH">Per month</option>
            </select>
          </div>
        </div>

        <fieldset className="grid gap-3 sm:grid-cols-2">
          <legend className="mb-1 text-sm font-medium text-foreground">
            Ceiling{" "}
            <span className="font-normal text-muted-foreground">
              (at least one)
            </span>
          </legend>

          <div>
            <label htmlFor="limit-quantity" className="text-sm text-foreground">
              Maximum quantity
            </label>
            <input
              id="limit-quantity"
              type="number"
              min={0}
              step="any"
              value={maxQuantity}
              onChange={(event) => setMaxQuantity(event.target.value)}
              placeholder="e.g. 500000"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Counted in the measure&apos;s own unit — pages, tokens, GB-months.
            </p>
          </div>

          <div>
            <label htmlFor="limit-cost" className="text-sm text-foreground">
              Maximum cost ($)
            </label>
            <input
              id="limit-cost"
              type="number"
              min={0}
              step="0.01"
              value={maxCost}
              onChange={(event) => setMaxCost(event.target.value)}
              placeholder="e.g. 250.00"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              {costMicros !== null
                ? `Sent as ${costMicros.toLocaleString()} micros.`
                : "In your billing currency. Stored as micros."}
            </p>
          </div>
        </fieldset>

        <div>
          <label className="flex items-start gap-2 text-sm text-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={hardStop}
              onChange={(event) => setHardStop(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Stop work at the limit</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {hardStop
                  ? "Requests are refused once the ceiling is reached. Nothing is billed beyond it, and work stops until the period rolls over or the limit is raised."
                  : "Work continues past the ceiling and the overage is billed. Use this when an interruption costs more than the overage does."}
              </span>
            </span>
          </label>
        </div>

        <div>
          <label htmlFor="limit-note" className="text-sm font-medium text-foreground">
            Note <span className="text-muted-foreground">(optional)</span>
          </label>
          <input
            id="limit-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={500}
            placeholder="Q3 budget cap agreed with finance"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Recorded with the limit for administrative audit.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending || !hasCeiling}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save limit
          </button>
          {!hasCeiling && (
            <span className="text-xs text-muted-foreground">
              Enter a maximum quantity, a maximum cost, or both.
            </span>
          )}
        </div>

        {sessionLimits.length > 0 && (
          <div className="border-t border-border pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Set in this session
            </p>
            <ul className="mt-1.5 space-y-1">
              {sessionLimits.map((limit) => (
                <li key={limit.id} className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {KEY_LABEL[limit.limit_key] ?? limit.limit_key}
                  </span>{" "}
                  · {limit.period === "DAY" ? "per day" : "per month"}
                  {limit.max_quantity !== null &&
                    ` · max ${Number(limit.max_quantity).toLocaleString()}`}
                  {limit.max_cost_micros !== null &&
                    ` · max $${(limit.max_cost_micros / MICROS_PER_UNIT).toFixed(2)}`}
                  {limit.hard_stop ? " · stops work" : " · bills overage"}
                  {limit.note && ` · ${limit.note}`}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
};

export default SpendLimitForm;
