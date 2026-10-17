import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { FlightPath, type FlightPin, type PastPoint } from "@/components/helm/FlightPath";
import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { StartTour } from "@/components/helm/HelmTour";
import { InstrumentRing, MoneyByCurrency, StateBadge, VisibilityMeter, percent } from "@/components/helm/HelmBits";
import { PlanDiff } from "@/components/helm/PlanDiff";
import { WhatIfPanel, type Holdable } from "@/components/helm/WhatIfPanel";
import { formatHelmMoney, formatHelmMoneyCompact } from "@/components/helm/money";
import { HelmSkeleton } from "@/components/helm/ui/HelmSkeleton";
import { CalendarHeatmap, mondayOf, type HeatDay } from "@/components/helm/ui/CalendarHeatmap";
import { DeltaBadge } from "@/components/helm/ui/DeltaBadge";
import { KpiTile } from "@/components/helm/ui/KpiTile";
import { PillBars, type PillBar } from "@/components/helm/ui/PillBars";
import { RingGauge } from "@/components/helm/ui/RingGauge";
import { Sparkline } from "@/components/helm/ui/Sparkline";
import { StatusPill } from "@/components/helm/ui/StatusPill";
import { LoadFailed } from "@/components/common/LoadFailed";
import { helmPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { getHelmBacktest, getHelmCalendar, getHelmCockpit, helmKeys, listHelmBacktests, replanHelmBook } from "@/services/api/helm";
import { formatCalendarDate, formatCalendarMonthShort, formatNumber } from "@/utils/displayTime";
import { formatRelativeTime } from "@/utils/formatters";
import type { CashPoint, HelmCalendar, HelmCockpit as Cockpit, HelmMandate, HelmWhatIf, Instrument, TrendPoint } from "@/types/helm";

const NO_SCENARIO: Record<string, CashPoint[]> = {};
const NO_PROBABILITY: Record<string, number> = {};
const NO_DAYS: string[] = [];
const NO_TRENDS: Record<string, TrendPoint[]> = {};

// The heavy sections re-render only when their own data changes, not every time another query of
// the page arrives (the entrance must not stall the main thread; spec 85).
const MemoFlightPath = React.memo(FlightPath);
const MemoPlanDiff = React.memo(PlanDiff);
const MemoVisibility = React.memo(VisibilityMeter);
const MemoWhatIf = React.memo(WhatIfPanel);
const MemoHeatmap = React.memo(CalendarHeatmap);
const MemoPillBars = React.memo(PillBars);
const DAY_MS = 86_400_000;

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

const isPayment = (kind: string): boolean => kind === "schedule_payment" || kind === "approve_payment";

const pinsOf = (cockpit: Cockpit): Record<string, FlightPin[]> => {
  const pins: Record<string, FlightPin[]> = {};
  for (const action of cockpit.actions) {
    if (isPayment(action.kind) && action.run_date && action.currency) {
      (pins[action.currency] ??= []).push({ on: action.run_date, label: action.title });
    }
  }
  return pins;
};

/** The plan's payments a what-if may hold (the ones this person can read), once each. */
const holdableOf = (cockpit: Cockpit): Holdable[] => {
  const seen = new Set<string>();
  const out: Holdable[] = [];
  for (const action of cockpit.actions) {
    if (action.kind === "schedule_payment" && action.readable && action.promise_id && !seen.has(action.promise_id)) {
      seen.add(action.promise_id);
      out.push({ promiseId: action.promise_id, label: action.title });
    }
  }
  return out;
};

/** The book's main currency: the one its cash is in, else the one it pays in, else rupees. */
const primaryCurrencyOf = (cockpit: Cockpit): string =>
  Object.keys(cockpit.summary.cash ?? {}).sort()[0] ??
  Object.keys(cockpit.summary.payments_by_currency ?? {}).sort()[0] ??
  "INR";

/** Mandates holding now and seven days ago, from the instruments' daily trends (only mandates with a state then). */
const holdingWeekAgo = (instruments: readonly Instrument[], trends: Record<string, TrendPoint[]>, asOf: string | undefined) => {
  if (!asOf) {
    return null;
  }
  const weekAgo = Date.parse(asOf) - 7 * DAY_MS;
  let now = 0;
  let then = 0;
  let compared = 0;
  for (const instrument of instruments) {
    const old = [...(trends[instrument.mandate_id] ?? [])].reverse().find((p) => p.state && Date.parse(p.on) <= weekAgo);
    if (!old) {
      continue;
    }
    compared += 1;
    now += instrument.state === "holding" ? 1 : 0;
    then += old.state === "holding" ? 1 : 0;
  }
  return compared > 0 ? { change: now - then, compared } : null;
};

/** Active mandates, the same array while the mandates are (so the instruments grid can skip renders). */
const activeCache = new WeakMap<readonly HelmMandate[], HelmMandate[]>();
const activeOf = (mandates: readonly HelmMandate[]): HelmMandate[] => {
  let found = activeCache.get(mandates);
  if (!found) {
    found = mandates.filter((m) => m.status === "ACTIVE");
    activeCache.set(mandates, found);
  }
  return found;
};

const plural = (count: number, one: string, many: string): string => `${formatNumber(count)} ${count === 1 ? one : many}`;

/** "Propose mode · Guard armed": the autonomy the active mandates run in, in words. */
const autonomyWords = (instruments: readonly Instrument[]): { text: string; tone: "holding" | "sky" | "drifting" } => {
  const acting = instruments.filter((i) => i.mode === "act").length;
  const watching = instruments.filter((i) => i.mode === "watch").length;
  if (acting > 0) {
    return { text: `Act mode on ${plural(acting, "mandate", "mandates")} · Guard armed`, tone: "drifting" };
  }
  if (instruments.length > 0 && watching === instruments.length) {
    return { text: "Watch mode · Guard armed", tone: "sky" };
  }
  return { text: "Propose mode · Guard armed", tone: "holding" };
};

type Direction = "owe" | "owed";

/** One direction of the calendar, in one currency: day -> amount and words. */
const calendarDays = (calendar: HelmCalendar | undefined, direction: Direction, currency: string): Map<string, HeatDay> => {
  const out = new Map<string, HeatDay>();
  if (!calendar) {
    return out;
  }
  const wanted = direction === "owe" ? "WE_OWE" : "OWED_TO_US";
  for (const entry of calendar.entries) {
    if (entry.direction !== wanted || entry.currency !== currency) {
      continue;
    }
    out.set(entry.on, {
      value: entry.amount_micros,
      words: `${formatHelmMoney(entry.amount_micros, currency)} in ${plural(entry.count, "promise", "promises")}`,
    });
  }
  for (const hidden of calendar.hidden) {
    if (hidden.direction !== wanted) {
      continue;
    }
    const known = out.get(hidden.on);
    const extra = `${plural(hidden.count, "promise", "promises")} in a workspace you cannot read`;
    out.set(hidden.on, { value: known?.value ?? 0, words: known ? `${known.words}, and ${extra}` : extra });
  }
  return out;
};

/** The instruments, one per active mandate; re-rendered only when they change. */
const InstrumentsGrid = React.memo(function InstrumentsGrid({
  instruments,
  active,
  trends,
  mandatesPath,
}: {
  readonly instruments: readonly Instrument[];
  readonly active: readonly HelmMandate[];
  readonly trends: Record<string, TrendPoint[]>;
  readonly mandatesPath: string;
}) {
  const holding = instruments.filter((i) => i.state === "holding").length;
  return (
    <section aria-labelledby="helm-instruments" data-tour="instruments">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 id="helm-instruments" className="helm-label">
          Instruments
        </h2>
        {instruments.length > 0 ? (
          <span className="helm-faint text-xs">
            {formatNumber(holding)} holding · {formatNumber(instruments.filter((i) => i.state === "drifting").length)} drifting ·{" "}
            {formatNumber(instruments.filter((i) => i.state === "breached").length)} breached
          </span>
        ) : null}
      </div>
      {active.length === 0 ? (
        <div className="helm-panel p-6 text-sm">
          <p>No mandate is active yet. A mandate is a rule Helm keeps true, such as never paying above the agreed price.</p>
          <Link to={mandatesPath} className="helm-btn helm-btn-primary mt-4">
            Choose mandates
          </Link>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {instruments.map((instrument, index) => {
            const points = trends[instrument.mandate_id] ?? [];
            return (
              <li
                key={instrument.mandate_id}
                className="helm-panel helm-panel-lift helm-enter flex min-w-0 flex-col gap-3 p-4"
                style={{ ["--i" as string]: Math.min(index, 10) }}
                data-testid="helm-instrument"
                data-state={instrument.state}
              >
                <div className="flex items-start gap-3">
                  <InstrumentRing state={instrument.state} />
                  <div className="min-w-0 flex-1">
                    <p className="line-clamp-2 text-sm font-semibold leading-snug" title={instrument.title}>
                      {instrument.title}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <StateBadge state={instrument.state} />
                      <span className="helm-chip px-2 text-xs">{MODE_WORDS[instrument.mode] ?? instrument.mode}</span>
                    </div>
                  </div>
                </div>
                <div>
                  <p className="helm-figure text-xl font-semibold leading-tight tracking-tight">{instrument.figure}</p>
                  <p className="helm-muted mt-1 line-clamp-2 text-xs leading-relaxed" title={instrument.detail}>
                    {instrument.detail}
                  </p>
                </div>
                {points.length > 1 ? (
                  <div className="mt-auto">
                    <Sparkline
                      values={points.map((p) => p.value)}
                      tone={instrument.state}
                      width={280}
                      height={30}
                      className="h-[30px] w-full"
                      label={`${instrument.title}: over ${points.length} days, from ${formatCalendarDate(points[0]?.on)} to ${formatCalendarDate(points[points.length - 1]?.on)}, its figure ${
                        (points[points.length - 1]?.value ?? 0) > (points[0]?.value ?? 0)
                          ? "rose"
                          : (points[points.length - 1]?.value ?? 0) < (points[0]?.value ?? 0)
                            ? "fell"
                            : "held steady"
                      }.`}
                    />
                  </div>
                ) : null}
              </li>
            );
          })}
          {active
            .filter((m) => !instruments.some((i) => i.mandate_id === m.id))
            .map((mandate) => (
              <li key={mandate.id} className="helm-panel flex gap-3 p-4">
                <InstrumentRing state="unknown" />
                <div className="min-w-0">
                  <p className="line-clamp-2 text-sm font-semibold" title={mandate.title}>
                    {mandate.title}
                  </p>
                  <div className="mt-1.5">
                    <StateBadge state="unknown" />
                  </div>
                  <p className="helm-muted mt-1.5 text-xs">Waiting for the next plan.</p>
                </div>
              </li>
            ))}
        </ul>
      )}
    </section>
  );
});

export const HelmCockpit: React.FC = () => {
  const { organizationId, orgSlug, withBook } = useHelm();
  const book = useHelmBook();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const cockpit = useQuery({
    queryKey: helmKeys.cockpit(organizationId, book.id),
    queryFn: () => getHelmCockpit(organizationId, book.id),
    refetchInterval: 30_000,
  });
  const calendar = useQuery({
    queryKey: helmKeys.calendar(organizationId, book.id),
    queryFn: () => getHelmCalendar(organizationId, book.id),
    staleTime: 60_000,
  });
  const replan = useMutation({
    mutationFn: () => replanHelmBook(organizationId, book.id),
    onSuccess: (data) => {
      queryClient.setQueryData(helmKeys.cockpit(organizationId, book.id), data);
      void queryClient.invalidateQueries({ queryKey: helmKeys.calendar(organizationId, book.id) });
      toast.success("Re-planned with what Helm can see now.");
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not re-plan.")),
  });

  // Scrubbing left into the past: the latest finished replay's cash history and what Helm would
  // have done on those days (brief §10.1).
  const replays = useQuery({
    queryKey: helmKeys.backtests(organizationId, book.id),
    queryFn: () => listHelmBacktests(organizationId, book.id),
  });
  const latestDone = replays.data?.find((row) => row.status === "DONE")?.id ?? null;
  // The replay's history only feeds "Look back"; it is large, so it loads after the entrance.
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(true), 1_200);
    return () => window.clearTimeout(timer);
  }, []);
  const replay = useQuery({
    queryKey: helmKeys.backtest(organizationId, latestDone ?? "none"),
    queryFn: () => getHelmBacktest(organizationId, latestDone as string),
    enabled: latestDone !== null && settled,
  });
  const [lookBack, setLookBack] = useState(0);
  const history = replay.data?.result?.cash_history ?? {};
  const pastDays = useMemo(() => {
    const ons = Object.values(history).flat().map((p) => p.on).sort();
    const first = ons[0];
    const today = cockpit.data?.summary.as_of;
    return first && today ? Math.max(0, Math.round((Date.parse(today) - Date.parse(first)) / DAY_MS)) : 0;
  }, [history, cockpit.data?.summary.as_of]);
  const past = useMemo(() => {
    const today = cockpit.data?.summary.as_of;
    if (!today || lookBack === 0) {
      return {};
    }
    const from = Date.parse(today) - lookBack * DAY_MS;
    const out: Record<string, PastPoint[]> = {};
    for (const [currency, points] of Object.entries(history)) {
      out[currency] = points.filter((p) => Date.parse(p.on) >= from).map((p) => ({ on: p.on, micros: p.amount_micros }));
    }
    return out;
  }, [history, lookBack, cockpit.data?.summary.as_of]);
  const pastEvents = useMemo(() => {
    const today = cockpit.data?.summary.as_of;
    if (!today || lookBack === 0) {
      return [];
    }
    const from = Date.parse(today) - lookBack * DAY_MS;
    return (replay.data?.result?.timeline ?? [])
      .filter((day) => Date.parse(day.on) >= from)
      .flatMap((day) => day.events.map((event) => ({ on: day.on, ...event })));
  }, [replay.data, lookBack, cockpit.data?.summary.as_of]);

  const floors = useMemo(() => floorsOf(cockpit.data?.mandates ?? []), [cockpit.data]);
  const pins = useMemo(() => (cockpit.data ? pinsOf(cockpit.data) : {}), [cockpit.data]);
  const holdable = useMemo(() => (cockpit.data ? holdableOf(cockpit.data) : []), [cockpit.data]);
  // A what-if belongs to the book it was asked about; switching books drops it.
  const [whatIf, setWhatIf] = useState<{ bookId: string; result: HelmWhatIf } | null>(null);
  const shownWhatIf = whatIf?.bookId === book.id ? whatIf.result : null;
  const onWhatIf = useCallback((result: HelmWhatIf | null) => setWhatIf(result ? { bookId: book.id, result } : null), [book.id]);
  const cashCurrencies = useMemo(() => Object.keys(cockpit.data?.summary.cash ?? {}).sort(), [cockpit.data]);
  const [direction, setDirection] = useState<Direction>("owe");
  const currency = cockpit.data ? primaryCurrencyOf(cockpit.data) : "INR";
  const heat = useMemo(() => calendarDays(calendar.data, direction, currency), [calendar.data, direction, currency]);
  // Money expected in, as markers on the flight path (every currency the calendar knows).
  const receipts = useMemo(() => {
    const out: Record<string, FlightPin[]> = {};
    for (const entry of calendar.data?.entries ?? []) {
      if (entry.direction === "OWED_TO_US") {
        (out[entry.currency] ??= []).push({
          on: entry.on,
          label: `${formatHelmMoney(entry.amount_micros, entry.currency)} expected from customers (${plural(entry.count, "promise", "promises")})`,
        });
      }
    }
    return out;
  }, [calendar.data]);

  // The performance gate (spec 85): when the cockpit first shows its data, once per page load.
  const ready = Boolean(cockpit.data);
  useEffect(() => {
    if (ready && typeof performance !== "undefined" && performance.getEntriesByName("helm:cockpit-ready").length === 0) {
      performance.mark("helm:cockpit-ready");
    }
  }, [ready]);

  const openDay = useCallback(
    (iso: string): void => {
      const target = withBook(helmPath(orgSlug, "ledger"));
      navigate(`${target}${target.includes("?") ? "&" : "?"}view=${direction}&due=${iso}`);
    },
    [direction, navigate, orgSlug, withBook],
  );

  if (cockpit.isLoading) {
    return <HelmSkeleton label="Reading the plan…" />;
  }
  if (cockpit.isError || !cockpit.data) {
    return <LoadFailed what="the cockpit" onRetry={() => void cockpit.refetch()} />;
  }

  const { plan, summary, actions, recent, value_held: valueHeld, mandates } = cockpit.data;
  const trends = cockpit.data.trends ?? NO_TRENDS;
  const instruments = summary.instruments ?? [];
  const active = activeOf(mandates);
  const forecast = summary.forecast ?? summary.simulation ?? null;
  const readOnly = book.read_only;
  // With a what-if, both lines come from the same fresh simulation, so they differ only by the change.
  const flightPaths = shownWhatIf ? shownWhatIf.baseline.cash_paths : forecast?.cash_paths ?? {};
  const flightDays = Object.values(flightPaths)[0]?.length ?? 0;
  const today = summary.as_of ?? new Date().toISOString().slice(0, 10);

  // ---- The KPI strip: only figures that are in the data. ----
  const holding = instruments.filter((i) => i.state === "holding").length;
  const weekAgo = holdingWeekAgo(instruments, trends, summary.as_of);
  const floorCurrency = Object.keys(floors).sort()[0] ?? currency;
  const probability = forecast?.floor_probability?.[floorCurrency];
  const lowest = (forecast?.cash_paths?.[floorCurrency] ?? []).reduce<CashPoint | null>(
    (low, point) => (low === null || point.p10 < low.p10 ? point : low),
    null,
  );
  const risks = summary.risks ?? [];
  const riskHere = risks.filter((r) => r.currency === currency && r.amount_micros !== null);
  const riskMicros = riskHere.reduce((sum, r) => sum + (r.amount_micros ?? 0), 0);
  const counts = summary.counts ?? { actions: actions.length, payments: 0, needs_approval: 0, blocked: 0 };
  const paymentsMicros = summary.payments_by_currency?.[currency] ?? 0;
  const heldMicros = valueHeld?.total?.[currency] ?? 0;
  const heldMonths = valueHeld ? Object.keys(valueHeld.by_month).sort() : [];
  const heldSeries = heldMonths.map((month) => (valueHeld?.by_month[month]?.[currency] ?? 0) / 1_000_000);
  const compactMoney = (micros: number): string => formatHelmMoneyCompact(micros, currency);
  const autonomy = autonomyWords(instruments);

  // ---- Payments by week: the plan's payments in the main currency, the next eight weeks. ----
  const weekStart = Date.parse(`${mondayOf(today)}T00:00:00Z`);
  const weeks: PillBar[] = Array.from({ length: 8 }, (_, index) => {
    const from = weekStart + index * 7 * DAY_MS;
    const lines = actions.filter((a) => {
      if (!isPayment(a.kind) || a.currency !== currency || !a.run_date || a.amount_micros === null) {
        return false;
      }
      const on = Date.parse(`${a.run_date}T00:00:00Z`);
      return on >= from && on < from + 7 * DAY_MS;
    });
    const value = lines.reduce((sum, a) => sum + (a.amount_micros ?? 0), 0);
    const monday = new Date(from).toISOString().slice(0, 10);
    return {
      key: monday,
      // "Oct 12", then the day alone until the month changes ("19", "26", "Nov 2").
      label:
        index === 0 || monday.slice(5, 7) !== new Date(from - 7 * DAY_MS).toISOString().slice(5, 7)
          ? `${formatCalendarMonthShort(Number(monday.slice(0, 4)), Number(monday.slice(5, 7)))} ${Number(monday.slice(8, 10))}`
          : String(Number(monday.slice(8, 10))),
      value,
      words: lines.length === 0 ? "no payments planned" : `${formatHelmMoney(value, currency)} in ${plural(lines.length, "payment", "payments")}`,
    };
  });

  // ---- The calendar in words. ----
  const heatTotal = [...heat.values()].reduce((sum, d) => sum + d.value, 0);
  const busiest = [...heat.entries()].sort((a, b) => b[1].value - a[1].value)[0];
  const heatSummary =
    heat.size === 0
      ? `Nothing ${direction === "owe" ? "we owe" : "owed to us"} falls due in the next 12 weeks.`
      : `Over the next 12 weeks, ${formatHelmMoney(heatTotal, currency)} ${direction === "owe" ? "we owe" : "is owed to us"} falls due on ${plural(heat.size, "day", "days")}` +
        (busiest ? `; the largest day is ${formatCalendarDate(busiest[0])}, with ${busiest[1].words}.` : ".");

  return (
    <div className="mx-auto max-w-[1400px] space-y-5">
      <header className="helm-enter flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="helm-label">{book.name}</p>
          <h1 className="helm-title mt-1">Cockpit</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
            <p className="helm-muted text-sm">
              {plan
                ? `Plan ${plan.version} · ${PLAN_STATUS_WORDS[plan.status] ?? plan.status} · made ${formatRelativeTime(plan.created_at)}.`
                : "No plan yet. Helm plans when a mandate is active and it can see your documents."}
            </p>
            {instruments.length > 0 ? <StatusPill tone={autonomy.tone}>{autonomy.text}</StatusPill> : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {book.sample ? <StartTour /> : null}
          {!readOnly ? (
            <button
              type="button"
              className="helm-btn helm-btn-primary helm-btn-hero"
              onClick={() => replan.mutate()}
              disabled={replan.isPending}
              aria-busy={replan.isPending}
            >
              {replan.isPending ? <span className="helm-radar" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
              {replan.isPending ? "Planning…" : "Re-plan now"}
            </button>
          ) : null}
        </div>
      </header>

      {!readOnly && !book.sample && (book.first_run?.step ?? 4) < 4 ? (
        <p className="helm-panel helm-enter flex flex-wrap items-center justify-between gap-2 p-4 text-sm">
          <span>
            Helm is not fully set up yet: step <span className="helm-figure">{(book.first_run?.step ?? 0) + 1}</span> of{" "}
            <span className="helm-figure">4</span>.
          </span>
          <Link to={withBook(helmPath(orgSlug, "setup"))} className="helm-btn helm-btn-primary">
            Finish setting up
          </Link>
        </p>
      ) : null}

      {plan?.conflict ? (
        <section role="alert" className="helm-panel helm-enter border-[hsl(var(--helm-drifting)/0.55)] p-5 shadow-[0_0_32px_-12px_hsl(var(--helm-drifting)/0.6)]">
          <p className="flex items-start gap-2 font-semibold">
            <AlertTriangle className="helm-state-drifting mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {plan.conflict.headline}
          </p>
          {plan.conflict.options.length > 0 ? (
            <ul className="mt-3 grid gap-2 pl-6 text-sm sm:grid-cols-2">
              {plan.conflict.options.map((option) => (
                <li key={option} className="helm-panel-raised px-3 py-2">
                  {option}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      {instruments.length > 0 || counts.actions > 0 ? (
        <section aria-label="Key figures">
          <ul
            className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 sm:pb-0 lg:grid-cols-3 min-[1400px]:grid-cols-6 [&>li]:w-[78%] [&>li]:shrink-0 [&>li]:snap-start sm:[&>li]:w-auto"
            data-testid="helm-kpis"
          >
            <KpiTile
              index={1}
              testId="helm-kpi-holding"
              label="Mandates holding"
              value={holding}
              format={(n) => formatNumber(n)}
              suffix={`/ ${formatNumber(instruments.length)}`}
              visual={<RingGauge value={instruments.length ? holding / instruments.length : 0} tone="holding" size={40} thickness={3.5} ticks={false} />}
              delta={
                weekAgo && weekAgo.change !== 0 ? (
                  <DeltaBadge
                    direction={weekAgo.change > 0 ? "up" : "down"}
                    good={weekAgo.change > 0}
                    label={`${weekAgo.change > 0 ? "Up" : "Down"} ${Math.abs(weekAgo.change)} since 7 days ago`}
                  >
                    {formatNumber(Math.abs(weekAgo.change))}
                  </DeltaBadge>
                ) : undefined
              }
              detail={
                weekAgo
                  ? weekAgo.change === 0
                    ? "Same as 7 days ago."
                    : `${weekAgo.change > 0 ? "More" : "Fewer"} than 7 days ago (of ${plural(weekAgo.compared, "mandate", "mandates")} with a state then).`
                  : `${plural(instruments.length - holding, "needs", "need")} attention.`
              }
            />
            {probability !== undefined ? (
              <KpiTile
                index={2}
                testId="helm-kpi-floor"
                label="Cash floor confidence"
                value={Math.round(probability * 100)}
                format={(n) => `${formatNumber(Math.round(n))}%`}
                visual={
                  <RingGauge
                    value={probability}
                    tone={probability >= 0.9 ? "holding" : probability >= 0.6 ? "drifting" : "breached"}
                    size={46}
                    thickness={4}
                    ticks={false}
                  />
                }
                detail={
                  lowest
                    ? `Lowest cautious cash ${formatHelmMoney(lowest.p10, floorCurrency)} on ${formatCalendarDate(lowest.on)}.`
                    : "Of the simulated futures stay above the floor."
                }
              />
            ) : (
              <KpiTile
                index={2}
                testId="helm-kpi-floor"
                label="Lowest cautious cash"
                value={lowest?.p10 ?? 0}
                format={(n) => formatHelmMoneyCompact(n, floorCurrency)}
                detail={lowest ? `On ${formatCalendarDate(lowest.on)}. No cash floor is set.` : "No cash forecast yet."}
              />
            )}
            <KpiTile
              index={3}
              testId="helm-kpi-risk"
              label="Money at risk"
              value={riskMicros}
              format={compactMoney}
              detail={
                risks.length === 0
                  ? "Nothing at risk in the plan's horizon."
                  : `${formatHelmMoney(riskMicros, currency)} across ${plural(risks.length, "risk", "risks")}.`
              }
              visual={
                <RingGauge value={null} tone={risks.length === 0 ? "holding" : "breached"} size={40} thickness={3.5} ticks={false}>
                  <span className="helm-figure text-xs font-semibold">{formatNumber(risks.length)}</span>
                </RingGauge>
              }
            />
            <KpiTile
              index={4}
              testId="helm-kpi-approval"
              label="Waiting for approval"
              value={counts.needs_approval}
              format={(n) => formatNumber(n)}
              suffix={`/ ${formatNumber(counts.actions)}`}
              visual={<RingGauge value={counts.actions ? counts.needs_approval / counts.actions : 0} tone="sky" size={40} thickness={3.5} ticks={false} />}
              detail={counts.blocked > 0 ? `${plural(counts.blocked, "action", "actions")} blocked by the Guard.` : "None blocked by the Guard."}
            />
            <KpiTile
              index={5}
              testId="helm-kpi-payments"
              label="Payments planned"
              value={paymentsMicros}
              format={compactMoney}
              detail={`${plural(counts.payments, "payment", "payments")}, ${formatHelmMoney(paymentsMicros, currency)}.`}
            />
            <KpiTile
              index={6}
              tone="primary"
              testId="helm-kpi-value"
              label="Value held, confirmed"
              value={heldMicros}
              format={compactMoney}
              detail={
                valueHeld
                  ? valueHeld.events > 0
                    ? `${plural(valueHeld.events, "outcome", "outcomes")} with evidence.`
                    : "Nothing proven yet."
                  : "Hidden: you cannot read this workspace."
              }
              visual={heldSeries.length > 1 ? <Sparkline values={heldSeries} tone="accent" width={64} height={30} /> : undefined}
            />
          </ul>
        </section>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-12">
        <section aria-labelledby="helm-flight" className="helm-panel helm-enter min-w-0 p-5 xl:col-span-8" style={{ ["--i" as string]: 7 }} data-tour="flight">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 id="helm-flight" className="helm-label">
                Flight path · next {formatNumber(flightDays)} days of cash
              </h2>
              {probability !== undefined ? (
                <p className="helm-muted mt-1 text-xs">
                  {percent(probability)} of {formatNumber(forecast?.futures ?? 1000)} simulated futures stay above the floor.
                </p>
              ) : null}
            </div>
            {shownWhatIf ? <span className="helm-chip helm-chip-accent">Showing a what-if</span> : null}
          </div>
          <MemoFlightPath
            paths={flightPaths}
            floors={floors}
            pins={pins}
            past={past}
            today={summary.as_of}
            floorProbability={shownWhatIf ? shownWhatIf.baseline.floor_probability : forecast?.floor_probability ?? NO_PROBABILITY}
            scenario={shownWhatIf?.scenario.cash_paths ?? NO_SCENARIO}
            runDays={summary.run_days ?? NO_DAYS}
            receipts={receipts}
          />
          {shownWhatIf ? (
            <p className="helm-muted mt-1 text-xs">
              The solid line is today&apos;s plan and the dashed line the what-if, both simulated just now with the same
              futures. Nothing has changed in the real plan.
            </p>
          ) : null}
          {pastDays > 0 ? (
            <div className="helm-panel-raised mt-4 space-y-2 p-3.5" data-testid="helm-scrub">
              <label className="flex flex-col gap-1 text-xs sm:flex-row sm:items-center sm:gap-3">
                <span className="helm-label shrink-0">Look back</span>
                <input
                  type="range"
                  className="w-full sm:max-w-sm"
                  min={-pastDays}
                  max={0}
                  step={1}
                  value={-lookBack}
                  aria-valuetext={lookBack === 0 ? "Today only" : `${lookBack} days into the replayed past`}
                  onChange={(event) => setLookBack(-Number(event.target.value))}
                />
                <span className="helm-figure shrink-0 font-semibold">{lookBack === 0 ? "today" : `${lookBack} days`}</span>
              </label>
              <p className="helm-muted text-xs">
                Drag left to replay the past from the Time machine's latest run: the grey line is the cash Helm saw,
                and below is what it would have done.
              </p>
              {pastEvents.length > 0 ? (
                <ul className="max-h-40 space-y-1 overflow-y-auto text-xs" aria-label="What Helm would have done in the replayed past">
                  {pastEvents.slice(-12).map((event, index) => (
                    <li key={`${event.on}-${index}`}>
                      <span className="helm-figure helm-muted mr-2">{formatCalendarDate(event.on)}</span>
                      {event.summary}
                    </li>
                  ))}
                </ul>
              ) : lookBack > 0 ? (
                <p className="text-xs">Nothing Helm would have done in these days.</p>
              ) : null}
            </div>
          ) : null}
        </section>

        <div className="grid min-w-0 gap-5 sm:grid-cols-2 xl:col-span-4 xl:grid-cols-1">
          <section aria-labelledby="helm-calendar" className="helm-panel helm-enter min-w-0 p-5" style={{ ["--i" as string]: 8 }} data-testid="helm-calendar-panel">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 id="helm-calendar" className="helm-label">
                Promise calendar
              </h2>
              <div role="radiogroup" aria-label="Which promises" className="flex gap-1">
                {(["owe", "owed"] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={direction === option}
                    className="helm-chip"
                    onClick={() => setDirection(option)}
                  >
                    {option === "owe" ? "We owe" : "Owed to us"}
                  </button>
                ))}
              </div>
            </div>
            {calendar.isError ? (
              <LoadFailed what="the calendar" onRetry={() => void calendar.refetch()} />
            ) : (
              <MemoHeatmap
                key={direction}
                today={today}
                weeks={12}
                days={heat}
                direction={direction}
                label={`Promise calendar: ${direction === "owe" ? "what we owe" : "what is owed to us"} in ${currency}, the next 12 weeks`}
                summary={heatSummary}
                onOpen={openDay}
                testId="helm-calendar"
              />
            )}
          </section>
          <section aria-labelledby="helm-weeks" className="helm-panel helm-enter min-w-0 p-5" style={{ ["--i" as string]: 9 }}>
            <div className="mb-3 flex items-baseline justify-between gap-2">
              <h2 id="helm-weeks" className="helm-label">
                Payments by week
              </h2>
              <span className="helm-faint text-xs">{currency}, next 8 weeks</span>
            </div>
            <MemoPillBars
              bars={weeks}
              caption={`The plan's payments in ${currency} for each of the next eight weeks, by the Monday each week starts on.`}
              periodHeader="Week of"
              valueHeader="Planned payments"
              testId="helm-weeks"
            />
          </section>
        </div>
      </div>

      <InstrumentsGrid instruments={instruments} active={active} trends={trends} mandatesPath={withBook(helmPath(orgSlug, "mandates"))} />

      <div className="grid items-start gap-5 lg:grid-cols-2 xl:grid-cols-3">
        {!readOnly ? (
          <MemoWhatIf
            key={book.id}
            organizationId={organizationId}
            bookId={book.id}
            currencies={cashCurrencies}
            holdable={holdable}
            result={shownWhatIf}
            onResult={onWhatIf}
          />
        ) : null}
        <section aria-labelledby="helm-value" className="helm-panel p-5">
          <h2 id="helm-value" className="helm-label">
            Value held
          </h2>
          <div className="helm-numeral mt-2 text-2xl">
            {valueHeld ? <MoneyByCurrency totals={valueHeld.total} empty="Nothing proven yet" /> : <span className="helm-muted text-sm">Hidden: you cannot read this workspace.</span>}
          </div>
          {valueHeld ? (
            <p className="helm-muted mt-1 text-xs">
              {valueHeld.rule} {valueHeld.events > 0 ? `${valueHeld.events} outcomes so far.` : ""}
            </p>
          ) : null}
        </section>
        <MemoVisibility visibility={summary.visibility} />
      </div>

      <section aria-labelledby="helm-plan" className="helm-panel min-w-0 p-5" data-tour="plan">
        <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="helm-plan" className="helm-label">
            The plan
          </h2>
          {summary.counts ? (
            <p className="helm-muted text-xs">
              {summary.counts.actions} actions · {summary.counts.needs_approval} waiting for approval · {summary.counts.blocked} blocked
            </p>
          ) : null}
        </div>
        <MemoPlanDiff actions={actions} readOnly={readOnly} />
      </section>

      {recent.length > 0 ? (
        <section aria-labelledby="helm-recent" className="helm-panel p-5">
          <h2 id="helm-recent" className="helm-label">
            Done recently
          </h2>
          <MemoPlanDiff actions={recent} readOnly={readOnly} />
        </section>
      ) : null}
    </div>
  );
};

export default HelmCockpit;
