import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { FlightPath, type FlightPin } from "@/components/helm/FlightPath";
import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { InstrumentRing, MoneyByCurrency, StateBadge, VisibilityMeter } from "@/components/helm/HelmBits";
import { PlanDiff } from "@/components/helm/PlanDiff";
import { LoadFailed } from "@/components/common/LoadFailed";
import { helmPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { getHelmCockpit, helmKeys, replanHelmBook } from "@/services/api/helm";
import { formatRelativeTime } from "@/utils/formatters";
import type { HelmCockpit as Cockpit, HelmMandate } from "@/types/helm";

const MODE_WORDS = { watch: "Watch", propose: "Propose", act: "Act" } as const;

const PLAN_STATUS_WORDS: Record<string, string> = {
  OPTIMAL: "best plan found",
  APPROXIMATE: "a good plan (the solver ran out of time)",
  CONFLICT: "mandates conflict",
  EMPTY: "nothing to schedule",
};

/** Floors from active "keep cash above" mandates, per currency. */
const floorsOf = (mandates: readonly HelmMandate[]): Record<string, number> => {
  const floors: Record<string, number> = {};
  for (const mandate of mandates) {
    if (mandate.status !== "ACTIVE" || mandate.template !== "keep_cash_above") {
      continue;
    }
    const floor = (mandate.spec?.params as { floor?: { amount_micros?: number; currency?: string } } | undefined)?.floor;
    if (floor?.currency && typeof floor.amount_micros === "number") {
      floors[floor.currency] = Math.max(floors[floor.currency] ?? Number.NEGATIVE_INFINITY, floor.amount_micros);
    }
  }
  return floors;
};

const pinsOf = (cockpit: Cockpit): Record<string, FlightPin[]> => {
  const pins: Record<string, FlightPin[]> = {};
  for (const action of cockpit.actions) {
    if ((action.kind === "schedule_payment" || action.kind === "approve_payment") && action.run_date && action.currency) {
      (pins[action.currency] ??= []).push({ on: action.run_date, label: action.title });
    }
  }
  return pins;
};

export const HelmCockpit: React.FC = () => {
  const { organizationId, orgSlug, withBook } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const cockpit = useQuery({
    queryKey: helmKeys.cockpit(organizationId, book.id),
    queryFn: () => getHelmCockpit(organizationId, book.id),
    refetchInterval: 30_000,
  });
  const replan = useMutation({
    mutationFn: () => replanHelmBook(organizationId, book.id),
    onSuccess: (data) => {
      queryClient.setQueryData(helmKeys.cockpit(organizationId, book.id), data);
      toast.success("Re-planned with what Helm can see now.");
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not re-plan.")),
  });

  const floors = useMemo(() => floorsOf(cockpit.data?.mandates ?? []), [cockpit.data]);
  const pins = useMemo(() => (cockpit.data ? pinsOf(cockpit.data) : {}), [cockpit.data]);

  if (cockpit.isLoading) {
    return (
      <p className="helm-muted text-sm" role="status">
        Reading the plan…
      </p>
    );
  }
  if (cockpit.isError || !cockpit.data) {
    return <LoadFailed what="the cockpit" onRetry={() => void cockpit.refetch()} />;
  }

  const { plan, summary, actions, recent, value_held: valueHeld, mandates } = cockpit.data;
  const instruments = summary.instruments ?? [];
  const active = mandates.filter((m) => m.status === "ACTIVE");
  const forecast = summary.forecast ?? summary.simulation ?? null;
  const readOnly = book.read_only;

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Cockpit</h1>
          <p className="helm-muted mt-1 text-sm">
            {plan
              ? `Plan ${plan.version}: ${PLAN_STATUS_WORDS[plan.status] ?? plan.status}, made ${formatRelativeTime(plan.created_at)}.`
              : "No plan yet. Helm plans when a mandate is active and it can see your documents."}
          </p>
        </div>
        {!readOnly ? (
          <button type="button" className="helm-btn" onClick={() => replan.mutate()} disabled={replan.isPending}>
            <RefreshCw className={`h-3.5 w-3.5 ${replan.isPending ? "animate-spin motion-reduce:animate-none" : ""}`} aria-hidden="true" />
            {replan.isPending ? "Planning…" : "Re-plan now"}
          </button>
        ) : null}
      </div>

      {plan?.conflict ? (
        <section role="alert" className="helm-panel border-l-4 p-4" style={{ borderLeftColor: "hsl(var(--helm-drifting))" }}>
          <p className="flex items-start gap-2 font-semibold">
            <AlertTriangle className="helm-state-drifting mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {plan.conflict.headline}
          </p>
          {plan.conflict.options.length > 0 ? (
            <ul className="helm-muted mt-2 list-disc space-y-1 pl-9 text-sm">
              {plan.conflict.options.map((option) => (
                <li key={option}>{option}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      <section aria-labelledby="helm-instruments">
        <h2 id="helm-instruments" className="helm-label mb-2">
          Instruments
        </h2>
        {active.length === 0 ? (
          <div className="helm-panel p-5 text-sm">
            <p>No mandate is active yet. A mandate is a rule Helm keeps true, such as never paying above the agreed price.</p>
            <Link to={withBook(helmPath(orgSlug, "mandates"))} className="helm-btn helm-btn-primary mt-3">
              Choose mandates
            </Link>
          </div>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {instruments.map((instrument) => (
              <li key={instrument.mandate_id} className="helm-panel flex gap-3 p-4" data-testid="helm-instrument">
                <InstrumentRing state={instrument.state} />
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">{instrument.title}</p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2">
                    <StateBadge state={instrument.state} />
                    <span className="helm-chip">{MODE_WORDS[instrument.mode] ?? instrument.mode}</span>
                  </div>
                  <p className="helm-figure mt-1.5 text-base font-semibold">{instrument.figure}</p>
                  <p className="helm-muted mt-0.5 text-xs">{instrument.detail}</p>
                </div>
              </li>
            ))}
            {active
              .filter((m) => !instruments.some((i) => i.mandate_id === m.id))
              .map((mandate) => (
                <li key={mandate.id} className="helm-panel flex gap-3 p-4">
                  <InstrumentRing state="unknown" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{mandate.title}</p>
                    <StateBadge state="unknown" />
                    <p className="helm-muted mt-1 text-xs">Waiting for the next plan.</p>
                  </div>
                </li>
              ))}
          </ul>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <section aria-labelledby="helm-flight" className="helm-panel p-4 lg:col-span-2">
          <h2 id="helm-flight" className="helm-label mb-2">
            Flight path · next {forecast?.cash_paths ? Object.values(forecast.cash_paths)[0]?.length ?? 0 : 0} days of cash
          </h2>
          <FlightPath
            paths={forecast?.cash_paths ?? {}}
            floors={floors}
            pins={pins}
            today={summary.as_of}
            floorProbability={forecast?.floor_probability ?? {}}
          />
        </section>
        <div className="space-y-4">
          <section aria-labelledby="helm-value" className="helm-panel p-4">
            <h2 id="helm-value" className="helm-label">
              Value held
            </h2>
            <div className="mt-2 text-xl font-semibold">
              {valueHeld ? <MoneyByCurrency totals={valueHeld.total} empty="Nothing proven yet" /> : <span className="helm-muted text-sm">Hidden: you cannot read this workspace.</span>}
            </div>
            {valueHeld ? (
              <p className="helm-muted mt-1 text-xs">
                {valueHeld.rule} {valueHeld.events > 0 ? `${valueHeld.events} outcomes so far.` : ""}
              </p>
            ) : null}
          </section>
          <VisibilityMeter visibility={summary.visibility} />
        </div>
      </div>

      <section aria-labelledby="helm-plan" className="helm-panel p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="helm-plan" className="helm-label">
            The plan
          </h2>
          {summary.counts ? (
            <p className="helm-muted text-xs">
              {summary.counts.actions} actions · {summary.counts.needs_approval} waiting for approval · {summary.counts.blocked} blocked
            </p>
          ) : null}
        </div>
        <PlanDiff actions={actions} readOnly={readOnly} />
      </section>

      {recent.length > 0 ? (
        <section aria-labelledby="helm-recent" className="helm-panel p-4">
          <h2 id="helm-recent" className="helm-label">
            Done recently
          </h2>
          <PlanDiff actions={recent} readOnly={readOnly} />
        </section>
      ) : null}
    </div>
  );
};

export default HelmCockpit;
