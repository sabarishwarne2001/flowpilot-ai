import React, { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CircleCheck, CircleHelp, Download, FileText, History, Lock, TrendingUp } from "lucide-react";

import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money, MoneyByCurrency } from "@/components/helm/HelmBits";
import { formatHelmMoney, formatHelmMoneyCompact } from "@/components/helm/money";
import { HelmPageHeader } from "@/components/helm/ui/HelmPageHeader";
import { HelmSkeleton } from "@/components/helm/ui/HelmSkeleton";
import { KpiTile } from "@/components/helm/ui/KpiTile";
import { PillBars, type PillBar } from "@/components/helm/ui/PillBars";
import { RingGauge } from "@/components/helm/ui/RingGauge";
import { LoadFailed } from "@/components/common/LoadFailed";
import { workItemDetailsPath } from "@/routes/tenantPaths";
import { downloadBlob } from "@/services/api/audit";
import { errorMessage } from "@/services/api/errors";
import {
  downloadHelmBacktestPdf,
  getHelmBacktest,
  helmKeys,
  listHelmBacktests,
  requestHelmBacktest,
} from "@/services/api/helm";
import { formatCalendarDate, formatCalendarMonthShort, formatNumber } from "@/utils/displayTime";
import { formatRelativeTime } from "@/utils/formatters";
import type { BacktestFinding, BacktestMandateResult, HelmBacktest, Honesty } from "@/types/helm";

/**
 * The Time machine (brief §9.1): what Helm would have done over a past period, replayed from the
 * book's own documents. Every finding names the documents it stands on. Value is shown in three
 * honesty classes that are never added together: confirmed (evidence shows it happened), likely,
 * and potential (facts are missing). The summary sentence is written by code, not a model.
 */

const HONESTY: { key: Honesty; label: string; help: string; tone: "holding" | "sky" | "drifting" }[] = [
  { key: "confirmed", label: "Confirmed", help: "Evidence shows it happened: a credit note, a reviewer, a payment date.", tone: "holding" },
  { key: "likely", label: "Likely", help: "The rule fired and the facts are there, but nothing confirms the loss.", tone: "sky" },
  { key: "potential", label: "Potential", help: "Facts are missing; Helm would have asked.", tone: "drifting" },
];

const HONESTY_ICONS = { confirmed: CircleCheck, likely: TrendingUp, potential: CircleHelp } as const;

/** What Helm would have done, month by month: the replay's timeline counted per month. */
const monthsOf = (row: HelmBacktest): PillBar[] => {
  const counts = new Map<string, number>();
  for (const day of row.result?.timeline ?? []) {
    const month = day.on.slice(0, 7);
    counts.set(month, (counts.get(month) ?? 0) + day.events.length);
  }
  const out: PillBar[] = [];
  const [y0, m0] = row.period_start.slice(0, 7).split("-").map(Number) as [number, number];
  const last = row.period_end.slice(0, 7);
  for (let year = y0, month = m0; out.length < 36; month === 12 ? ((year += 1), (month = 1)) : (month += 1)) {
    const key = `${year}-${String(month).padStart(2, "0")}`;
    const value = counts.get(key) ?? 0;
    out.push({
      key,
      label: formatCalendarMonthShort(year, month),
      value,
      words: value === 0 ? "nothing" : `${formatNumber(value)} ${value === 1 ? "thing" : "things"} Helm would have done`,
    });
    if (key >= last) {
      break;
    }
  }
  return out;
};

const STATUS_WORDS: Record<HelmBacktest["status"], string> = {
  QUEUED: "Waiting to start",
  RUNNING: "Replaying the period…",
  DONE: "Done",
  FAILED: "Could not finish",
};

const running = (row: HelmBacktest | undefined): boolean => row?.status === "QUEUED" || row?.status === "RUNNING";

const Findings: React.FC<{ readonly result: BacktestMandateResult; readonly workspaceSlug: string | null }> = ({
  result,
  workspaceSlug,
}) => {
  const { orgSlug } = useHelm();
  if (result.findings.length === 0) {
    return <p className="helm-muted text-sm">Nothing to show in this period.</p>;
  }
  return (
    <ul className="helm-divide text-sm">
      {result.findings.map((finding: BacktestFinding, index) => (
        <li key={`${finding.on}-${index}`} className="flex flex-col gap-1 py-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p>
              <span className="helm-figure helm-muted mr-2 text-xs">{formatCalendarDate(finding.on)}</span>
              {finding.summary}
            </p>
            {finding.evidence.length > 0 ? (
              <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs">
                {finding.evidence.slice(0, 3).map((item, i) =>
                  workspaceSlug && item.work_item_id ? (
                    <Link
                      key={`${String(item.work_item_id)}-${i}`}
                      to={workItemDetailsPath(orgSlug, workspaceSlug, String(item.work_item_id))}
                      className="helm-accent inline-flex items-center gap-1 hover:underline"
                    >
                      <FileText className="h-3 w-3" aria-hidden="true" />
                      {String(item.document_number ?? item.document ?? "Document")}
                    </Link>
                  ) : (
                    <span key={i} className="helm-muted">
                      {String(item.document_number ?? item.document ?? "Document")}
                    </span>
                  ),
                )}
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-2 text-xs">
            <span className={`helm-chip helm-state-${HONESTY.find((h) => h.key === finding.honesty)?.tone ?? "unknown"}`}>
              {HONESTY.find((h) => h.key === finding.honesty)?.label ?? finding.honesty}
            </span>
            {finding.value_micros ? <Money micros={finding.value_micros} currency={finding.currency} /> : null}
          </div>
        </li>
      ))}
    </ul>
  );
};

/**
 * "Download the PDF": the finished replay as a report the owner can hand to an accountant or board.
 * Its footer prints the fingerprint shown under the button, so a reader can check it is Helm's.
 */
const ReplayPdf: React.FC<{ readonly row: HelmBacktest }> = ({ row }) => {
  const { organizationId } = useHelm();
  const pdf = useMutation({
    mutationFn: () => downloadHelmBacktestPdf(organizationId, row.id),
    onSuccess: (blob) =>
      downloadBlob(blob, row.report?.filename ?? `helm-replay-${row.period_start}-to-${row.period_end}.pdf`),
    onError: (error) => toast.error(errorMessage(error, "Helm could not make the PDF of this replay. Try again.")),
  });
  return (
    <button
      type="button"
      className="helm-btn shrink-0"
      onClick={() => pdf.mutate()}
      disabled={pdf.isPending}
      aria-busy={pdf.isPending}
      aria-describedby={row.report ? "helm-tm-fingerprint" : undefined}
      data-testid="helm-backtest-pdf"
    >
      <Download className="h-3.5 w-3.5" aria-hidden="true" />
      Download the PDF
    </button>
  );
};

/** A finished backtest: the summary, the three honesty classes, and each mandate's findings. */
export const BacktestReport: React.FC<{ readonly row: HelmBacktest; readonly workspaceSlug: string | null }> = ({
  row,
  workspaceSlug,
}) => {
  const result = row.result ?? {};
  const mandates = result.mandates ?? [];
  const [open, setOpen] = useState<string | null>(mandates.find((m) => m.count > 0)?.template ?? null);
  return (
    <div className="space-y-4" data-testid="helm-backtest-report">
      <div className="helm-panel helm-panel-strong helm-enter p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="helm-chip helm-chip-accent gap-1.5 px-3 py-1 text-[13px]" data-testid="helm-tm-showing">
              <History className="h-3.5 w-3.5" aria-hidden="true" />
              Showing the {formatNumber(row.months)}-month replay
            </p>
            <p className="helm-label mt-2">
              {formatCalendarDate(row.period_start)} to {formatCalendarDate(row.period_end)} · {row.months} months
              {row.finished_at ? ` · replayed ${formatRelativeTime(row.finished_at)}` : ""}
            </p>
          </div>
          {row.status === "DONE" ? <ReplayPdf row={row} /> : null}
        </div>
        <p className="mt-2 max-w-4xl text-lg leading-relaxed tracking-tight" data-testid="helm-backtest-summary">
          {row.summary}
        </p>
        <ul className="mt-5 grid gap-3 lg:grid-cols-3" aria-label="Value, in three classes never added together">
          {HONESTY.map((h, index) => {
            const totals = Object.entries(result.totals?.[h.key] ?? {}).filter(([, micros]) => micros !== 0);
            const [first, ...others] = totals;
            const Icon = HONESTY_ICONS[h.key];
            return (
              <KpiTile
                key={h.key}
                index={index + 1}
                testId={`helm-tm-${h.key}`}
                label={h.label}
                value={first ? first[1] : 0}
                format={(n) => (first ? formatHelmMoneyCompact(n, first[0]) : "Nothing")}
                visual={
                  <RingGauge value={null} tone={h.tone} size={40} thickness={3.5} ticks={false}>
                    <Icon className={`helm-state-${h.tone} h-4 w-4`} aria-hidden="true" />
                  </RingGauge>
                }
                detail={
                  <>
                    {first ? <span className="helm-figure block font-semibold text-[hsl(var(--helm-ink))]">{formatHelmMoney(first[1], first[0])}</span> : null}
                    {others.length > 0 ? (
                      <span className="block">
                        and <MoneyByCurrency totals={Object.fromEntries(others)} />
                      </span>
                    ) : null}
                    <span className="block">{h.help}</span>
                  </>
                }
              />
            );
          })}
        </ul>
        <p className="helm-muted mt-3 text-xs">{result.honesty_rule ?? "The three classes are never added together."}</p>
        {(result.timeline ?? []).length > 0 ? (
          <div className="mt-5">
            <p className="helm-label mb-2">What Helm would have done, month by month</p>
            <PillBars
              bars={monthsOf(row)}
              height={96}
              caption="How many things Helm would have done in each month of the replay."
              periodHeader="Month"
              valueHeader="Things Helm would have done"
              testId="helm-tm-months"
            />
          </div>
        ) : null}
        {row.report ? (
          <p id="helm-tm-fingerprint" className="helm-muted mt-2 text-xs">
            The PDF&apos;s footer carries this replay&apos;s fingerprint (SHA-256 of its stored result):{" "}
            <code className="helm-figure break-all">{row.report.result_sha256}</code>
          </p>
        ) : null}
      </div>

      <section aria-labelledby="helm-tm-mandates">
        <h2 id="helm-tm-mandates" className="helm-label mb-2">
          Mandate by mandate
        </h2>
        <ul className="space-y-2">
          {mandates.map((m) => {
            const expanded = open === m.template;
            const panel = `helm-tm-${m.template}`;
            return (
              <li key={m.template} className="helm-panel p-4" data-open={expanded ? "true" : undefined}>
                <button
                  type="button"
                  className="flex w-full flex-col gap-1 text-left sm:flex-row sm:items-center sm:justify-between"
                  aria-expanded={expanded}
                  aria-controls={panel}
                  onClick={() => setOpen(expanded ? null : m.template)}
                >
                  <span className="text-sm font-semibold">
                    {m.title}
                    <span className="helm-muted ml-2 font-normal">
                      {m.count === 0 ? "nothing in this period" : `${m.count} ${m.count === 1 ? "finding" : "findings"}`}
                    </span>
                  </span>
                  <span className="flex flex-wrap gap-3 text-xs">
                    {HONESTY.map((h) =>
                      Object.values(m.totals?.[h.key] ?? {}).some(Boolean) ? (
                        <span key={h.key} className="inline-flex items-center gap-1">
                          <span className="helm-muted">{h.label}</span>
                          <MoneyByCurrency totals={m.totals[h.key]} />
                        </span>
                      ) : null,
                    )}
                  </span>
                </button>
                {expanded ? (
                  <div id={panel} className="mt-3">
                    <Findings result={m} workspaceSlug={workspaceSlug} />
                    {m.truncated ? (
                      <p className="helm-muted mt-2 text-xs">Showing the first {m.findings.length}; the totals cover all of them.</p>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
};

export const HelmTimeMachine: React.FC = () => {
  const { organizationId, overview, canDecide } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const limit = book.kind === "SAMPLE" ? Math.max(overview.limits.backtest_months, 9) : overview.limits.backtest_months;
  const [months, setMonths] = useState(Math.min(limit, 6) || 1);

  const history = useQuery({
    queryKey: helmKeys.backtests(organizationId, book.id),
    queryFn: () => listHelmBacktests(organizationId, book.id),
  });
  const chosenId = params.get("backtest") ?? history.data?.[0]?.id ?? null;
  const chosen = useQuery({
    queryKey: helmKeys.backtest(organizationId, chosenId ?? "none"),
    queryFn: () => getHelmBacktest(organizationId, chosenId as string),
    enabled: chosenId !== null,
    refetchInterval: (query) => (running(query.state.data) ? 2_000 : false),
  });
  const wasRunning = running(chosen.data);
  React.useEffect(() => {
    if (!wasRunning && chosen.data) {
      void queryClient.invalidateQueries({ queryKey: helmKeys.backtests(organizationId, book.id) });
    }
  }, [wasRunning, chosen.data, queryClient, organizationId, book.id]);

  const run = useMutation({
    mutationFn: () => requestHelmBacktest(organizationId, { book_id: book.id, months }),
    onSuccess: (queued) => {
      setParams((current) => {
        const next = new URLSearchParams(current);
        next.set("backtest", queued.id);
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: helmKeys.backtests(organizationId, book.id) });
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not start the backtest.")),
  });

  const workspaceSlug = useMemo(
    () => overview.workspaces.find((w) => w.id === book.workspace_id)?.slug ?? null,
    [overview.workspaces, book.workspace_id],
  );

  if (history.isError) {
    return <LoadFailed what="the Time machine" onRetry={() => void history.refetch()} />;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6" data-tour="time-machine">
      <HelmPageHeader
        eyebrow={book.name}
        title="Time machine"
        description="What Helm would have done over a past period, replayed from your own documents: the day each one arrived is the day Helm would have seen it. Nothing is executed."
      />

      {canDecide ? (
        <form
          className="helm-panel helm-enter flex flex-col gap-3 p-4 sm:flex-row sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            run.mutate();
          }}
        >
          <label className="text-sm">
            <span className="helm-label block">Replay the last</span>
            <select
              className="helm-input mt-1"
              value={months}
              onChange={(event) => setMonths(Number(event.target.value))}
              aria-describedby="helm-tm-limit"
            >
              {Array.from({ length: limit }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n} {n === 1 ? "month" : "months"}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="helm-btn helm-btn-primary" disabled={run.isPending || wasRunning}>
            <History className="h-3.5 w-3.5" aria-hidden="true" />
            Replay with my mandates
          </button>
          <p id="helm-tm-limit" className="helm-muted text-xs sm:ml-auto sm:max-w-xs">
            {overview.access.preview && book.kind !== "SAMPLE" ? (
              <span className="inline-flex items-center gap-1">
                <Lock className="h-3 w-3" aria-hidden="true" />
                Helm preview replays up to 3 months, once a week.
              </span>
            ) : (
              `Your plan replays up to ${limit} months. With no active mandates, Helm replays the ones it recommends.`
            )}
          </p>
        </form>
      ) : null}

      {chosenId === null ? (
        history.isLoading ? (
          <HelmSkeleton label="Loading…" />
        ) : (
          <p className="helm-panel p-4 text-sm">No replays yet. Choose a period and replay it.</p>
        )
      ) : chosen.isError ? (
        <LoadFailed what="this replay" onRetry={() => void chosen.refetch()} />
      ) : !chosen.data || running(chosen.data) ? (
        <p className="helm-panel flex items-center gap-3 p-5 text-sm" role="status" aria-live="polite">
          <span className="helm-radar helm-accent" aria-hidden="true" />
          {chosen.data ? STATUS_WORDS[chosen.data.status] : "Loading…"}
        </p>
      ) : chosen.data.status === "FAILED" ? (
        <p className="helm-panel p-4 text-sm" role="alert">
          This replay could not finish: {chosen.data.error ?? "an unexpected error"}. Try again.
        </p>
      ) : (
        <BacktestReport key={chosen.data.id} row={chosen.data} workspaceSlug={book.kind === "SAMPLE" || workspaceSlug ? workspaceSlug : null} />
      )}

      {(history.data ?? []).length > 1 ? (
        <section aria-labelledby="helm-tm-history">
          <h2 id="helm-tm-history" className="helm-label mb-2">
            Earlier replays
          </h2>
          <ul className="helm-panel helm-divide overflow-hidden text-sm">
            {(history.data ?? []).map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="flex w-full flex-col gap-0.5 px-4 py-2.5 text-left hover:bg-[hsl(var(--helm-glass-top))] aria-[current=true]:bg-[hsl(var(--helm-accent)/0.1)] sm:flex-row sm:justify-between"
                  aria-current={row.id === chosenId ? "true" : undefined}
                  onClick={() =>
                    setParams((current) => {
                      const next = new URLSearchParams(current);
                      next.set("backtest", row.id);
                      return next;
                    })
                  }
                >
                  <span>
                    {row.months} months to {formatCalendarDate(row.period_end)}
                    {row.mandates.length > 0 ? ` · ${row.mandates.map((m) => m.title ?? m.template).join(", ")}` : ""}
                  </span>
                  <span className="helm-muted text-xs">{STATUS_WORDS[row.status]}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
};

export default HelmTimeMachine;
