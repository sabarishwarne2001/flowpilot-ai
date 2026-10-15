import React, { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, History, Lock } from "lucide-react";

import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money, MoneyByCurrency } from "@/components/helm/HelmBits";
import { LoadFailed } from "@/components/common/LoadFailed";
import { workItemDetailsPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { getHelmBacktest, helmKeys, listHelmBacktests, requestHelmBacktest } from "@/services/api/helm";
import { formatCalendarDate } from "@/utils/displayTime";
import type { BacktestFinding, BacktestMandateResult, HelmBacktest, Honesty } from "@/types/helm";

/**
 * The Time machine (brief §9.1): what Helm would have done over a past period, replayed from the
 * book's own documents. Every finding names the documents it stands on. Value is shown in three
 * honesty classes that are never added together: confirmed (evidence shows it happened), likely,
 * and potential (facts are missing). The summary sentence is written by code, not a model.
 */

const HONESTY: { key: Honesty; label: string; help: string }[] = [
  { key: "confirmed", label: "Confirmed", help: "Evidence shows it happened: a credit note, a reviewer, a payment date." },
  { key: "likely", label: "Likely", help: "The rule fired and the facts are there, but nothing confirms the loss." },
  { key: "potential", label: "Potential", help: "Facts are missing; Helm would have asked." },
];

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
    <ul className="divide-y divide-[var(--helm-line)] text-sm">
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
            <span className="helm-chip">{HONESTY.find((h) => h.key === finding.honesty)?.label ?? finding.honesty}</span>
            {finding.value_micros ? <Money micros={finding.value_micros} currency={finding.currency} /> : null}
          </div>
        </li>
      ))}
    </ul>
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
      <div className="helm-panel p-4">
        <p className="helm-label">
          {formatCalendarDate(row.period_start)} to {formatCalendarDate(row.period_end)} · {row.months} months
        </p>
        <p className="mt-1.5 text-base leading-relaxed" data-testid="helm-backtest-summary">
          {row.summary}
        </p>
        <dl className="mt-4 grid gap-3 sm:grid-cols-3">
          {HONESTY.map((h) => (
            <div key={h.key} className="rounded-lg border border-[var(--helm-line)] p-3">
              <dt className="helm-label">{h.label}</dt>
              <dd className="helm-figure mt-1 text-lg">
                <MoneyByCurrency totals={result.totals?.[h.key]} empty="Nothing" />
              </dd>
              <dd className="helm-muted mt-1 text-xs">{h.help}</dd>
            </div>
          ))}
        </dl>
        <p className="helm-muted mt-3 text-xs">{result.honesty_rule ?? "The three classes are never added together."}</p>
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
              <li key={m.template} className="helm-panel p-4">
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
    <div className="mx-auto max-w-5xl space-y-6" data-tour="time-machine">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Time machine</h1>
        <p className="helm-muted mt-1 text-sm">
          What Helm would have done over a past period, replayed from your own documents: the day each one arrived is the
          day Helm would have seen it. Nothing is executed.
        </p>
      </div>

      {canDecide ? (
        <form
          className="helm-panel flex flex-col gap-3 p-4 sm:flex-row sm:items-end"
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
          <p className="helm-muted text-sm" role="status">
            Loading…
          </p>
        ) : (
          <p className="helm-panel p-4 text-sm">No replays yet. Choose a period and replay it.</p>
        )
      ) : chosen.isError ? (
        <LoadFailed what="this replay" onRetry={() => void chosen.refetch()} />
      ) : !chosen.data || running(chosen.data) ? (
        <p className="helm-panel p-4 text-sm" role="status" aria-live="polite">
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
          <ul className="helm-panel divide-y divide-[var(--helm-line)] text-sm">
            {(history.data ?? []).map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="flex w-full flex-col gap-0.5 px-4 py-2 text-left hover:bg-[var(--helm-panel-raised)] sm:flex-row sm:justify-between"
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
