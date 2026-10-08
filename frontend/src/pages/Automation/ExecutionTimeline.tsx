import { formatTimestamp } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  Ban,
  CheckCircle2,
  ChevronRight,
  Clock,
  Filter,
  Loader2,
  RotateCcw,
  XCircle,
} from "lucide-react";

import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import {
  EXECUTION_STATUS_PRESENTATION,
  listExecutions,
} from "@/services/api/executions";
import type {
  AutomationExecution,
  AutomationExecutionStatus,
} from "@/services/api/executions";
import { automationKeys, workspaceScope } from "@/services/api/queryKeys";
import { listExecutionNodes } from "@/services/api/executions";
import { formatMicros } from "@/types/billing";
import { pollUnlessRefused } from "@/services/api/polling";

/**
 * ARCH41-S1:adaptive-timeline-poll. The timeline polled every 30 seconds, so a
 * reviewer who fired a test event watched a RUNNING row for up to half a
 * minute and concluded the engine was stuck. Poll quickly while anything is
 * in flight, slowly when nothing is, and not at all once the server has
 * refused (plan, permission) — `pollUnlessRefused` still decides that.
 */
const LIVE_POLL_MS = 2_000;
const IDLE_POLL_MS = 30_000;
const IN_FLIGHT: ReadonlySet<AutomationExecutionStatus> = new Set<AutomationExecutionStatus>([
  "QUEUED",
  "RUNNING",
]);

const timelinePollInterval = (query: {
  readonly state: {
    readonly error: unknown;
    readonly data?: { readonly items?: readonly AutomationExecution[] } | undefined;
  };
}): number | false => {
  if (pollUnlessRefused(IDLE_POLL_MS)(query) === false) {
    return false;
  }
  const items = query.state.data?.items ?? [];
  return items.some((execution) => IN_FLIGHT.has(execution.status))
    ? LIVE_POLL_MS
    : IDLE_POLL_MS;
};

const TONE_CLASSES: Record<string, string> = {
  ok: "border-emerald-500/40 bg-emerald-500/5",
  warn: "border-amber-500/50 bg-amber-500/10",
  danger: "border-destructive/50 bg-destructive/10",
  muted: "border-border bg-card",
};

/** "840 ms", "1.2 s", "2 min 05 s": a run's duration at a glance. */
export const formatDuration = (ms: number): string => {
  if (ms < 1_000) {
    return `${Math.max(0, Math.round(ms))} ms`;
  }
  if (ms < 60_000) {
    return `${(ms / 1_000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  }
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1_000);
  return `${minutes} min ${String(seconds).padStart(2, "0")} s`;
};

type DotTone = "ok" | "warn" | "danger" | "live" | "muted";

const DOT_CLASSES: Record<DotTone, string> = {
  ok: "bg-emerald-500 ring-emerald-500/20",
  warn: "bg-amber-500 ring-amber-500/25",
  danger: "bg-destructive ring-destructive/25",
  live: "bg-primary ring-primary/25 animate-pulse",
  muted: "bg-muted-foreground/60 ring-muted-foreground/15",
};

const DOT_LABEL: Record<DotTone, string> = {
  ok: "Completed",
  warn: "Blocked",
  danger: "Failed",
  live: "Running",
  muted: "Waiting",
};

const StatusDot: React.FC<{ readonly tone: DotTone; readonly label?: string }> = ({ tone, label }) => (
  <span
    role="img"
    aria-label={label ?? DOT_LABEL[tone]}
    className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ring-4 ${DOT_CLASSES[tone]}`}
  />
);

const DurationBadge: React.FC<{ readonly ms: number | null }> = ({ ms }) =>
  ms === null ? null : (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border bg-background/70 px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
      <Clock className="h-3 w-3" aria-hidden="true" />
      {formatDuration(ms)}
    </span>
  );

const executionTone = (execution: AutomationExecution): DotTone => {
  if (execution.status === "FAILED" || execution.status === "TIMED_OUT") {
    return "danger";
  }
  if (execution.is_suppressed) {
    return "warn";
  }
  if (IN_FLIGHT.has(execution.status)) {
    return "live";
  }
  return execution.status === "COMPLETED" ? "ok" : "muted";
};

/** A chain is as bad as its worst step: failed, then blocked, then still running. */
const chainTone = (executions: readonly AutomationExecution[]): DotTone => {
  const tones = executions.map(executionTone);
  for (const tone of ["danger", "warn", "live", "muted"] as const) {
    if (tones.includes(tone)) {
      return tone;
    }
  }
  return "ok";
};

const nodeTone = (status: string): DotTone => {
  const normalized = status.toUpperCase();
  if (normalized === "FAILED" || normalized === "TIMED_OUT") {
    return "danger";
  }
  if (normalized === "SUCCEEDED" || normalized === "COMPLETED" || normalized === "OK") {
    return "ok";
  }
  if (normalized === "RUNNING" || normalized === "PENDING" || normalized === "QUEUED") {
    return "live";
  }
  return "muted";
};

const StatusIcon: React.FC<{ status: AutomationExecutionStatus }> = ({
  status,
}) => {
  switch (status) {
    case "COMPLETED":
      return <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />;
    case "FAILED":
    case "TIMED_OUT":
      return <XCircle className="h-4 w-4 text-destructive shrink-0" />;
    case "SUPPRESSED_CYCLE":
      return <RotateCcw className="h-4 w-4 text-amber-600 shrink-0" />;
    case "SUPPRESSED_DEPTH":
      return <ArrowDown className="h-4 w-4 text-amber-600 shrink-0" />;
    case "BUDGET_EXHAUSTED":
      return <Ban className="h-4 w-4 text-amber-600 shrink-0" />;
    default:
      return <Clock className="h-4 w-4 text-muted-foreground shrink-0" />;
  }
};

const startOf = (execution: AutomationExecution): number => Date.parse(execution.started_at ?? execution.created_at);

const endOf = (execution: AutomationExecution): number =>
  execution.completed_at
    ? Date.parse(execution.completed_at)
    : startOf(execution) + Math.max(0, execution.duration_ms ?? 0);

/** First start to last finish across a chain's runs, or null when none has a duration yet. */
export const chainSpanMs = (executions: readonly AutomationExecution[]): number | null => {
  const timed = executions.filter((execution) => execution.duration_ms !== null || execution.completed_at);
  if (timed.length === 0) {
    return null;
  }
  const start = Math.min(...timed.map(startOf));
  const end = Math.max(...timed.map(endOf));
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : null;
};

const percentile = (sorted: readonly number[], p: number): number | null =>
  sorted.length === 0 ? null : sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]!;

const summarise = (items: readonly AutomationExecution[], chains: number) => {
  const durations = items
    .map((execution) => execution.duration_ms)
    .filter((ms): ms is number => ms !== null)
    .sort((a, b) => a - b);
  const completed = items.filter((execution) => execution.status === "COMPLETED").length;
  return {
    chains,
    runs: items.length,
    completed,
    completedPct: items.length ? Math.round((completed / items.length) * 100) : 0,
    blocked: items.filter((execution) => execution.is_suppressed).length,
    failed: items.filter((execution) => execution.status === "FAILED" || execution.status === "TIMED_OUT").length,
    medianMs: percentile(durations, 0.5),
    p95Ms: durations.length >= 5 ? percentile(durations, 0.95) : null,
  };
};

const KPI_TONE: Record<"ok" | "warn" | "danger", string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  danger: "text-destructive",
};

const Kpi: React.FC<{
  readonly label: string;
  readonly value: React.ReactNode;
  readonly hint?: string | undefined;
  readonly tone?: "ok" | "warn" | "danger" | undefined;
}> = ({ label, value, hint, tone }) => (
  <div className="rounded-lg border border-border bg-card px-3 py-2">
    <dt className="text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">{label}</dt>
    <dd className={`mt-0.5 text-xl font-semibold tabular-nums ${tone ? KPI_TONE[tone] : "text-foreground"}`}>{value}</dd>
    {hint ? <dd className="text-[11px] text-muted-foreground">{hint}</dd> : null}
  </div>
);

const BAR_TONE: Record<DotTone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  danger: "bg-destructive",
  live: "bg-primary animate-pulse",
  muted: "bg-muted-foreground/50",
};

/**
 * Phase 2 — a chain's runs on one time axis: where each started after the triggering event, how
 * long it waited in the queue (the faint lead-in) and how long it ran (the bar). Runs side by side
 * overlap; a rule set off by another starts after it. Click a run to jump to its card below.
 */
const ChainWaterfall: React.FC<{ readonly executions: readonly AutomationExecution[] }> = ({ executions }) => {
  const origin = Math.min(...executions.map((execution) => Date.parse(execution.created_at)));
  const end = Math.max(...executions.map(endOf), origin + 1);
  const span = Math.max(1, end - origin);
  const pct = (ms: number) => `${Math.max(0, Math.min(100, (ms / span) * 100))}%`;
  return (
    <div className="border-t border-border/60 px-4 py-3" role="group" aria-label="Runs on a time axis">
      <div className="mb-1.5 flex justify-between pl-[11.5rem] pr-16 text-[10.5px] tabular-nums text-muted-foreground">
        <span>0</span>
        <span>{formatDuration(span / 2)}</span>
        <span>{formatDuration(span)}</span>
      </div>
      <ol className="space-y-1">
        {executions.map((execution) => {
          const created = Date.parse(execution.created_at) - origin;
          const started = startOf(execution) - origin;
          const finished = endOf(execution) - origin;
          const tone = executionTone(execution);
          const name = execution.rule_name ?? `Rule ${execution.rule_id.slice(0, 8)}`;
          return (
            <li key={execution.id}>
              <button
                type="button"
                onClick={() =>
                  document.getElementById(`execution-${execution.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })
                }
                className="group flex w-full items-center gap-3 rounded px-1 py-0.5 text-left hover:bg-muted/50"
                title={`${name}: waited ${formatDuration(Math.max(0, started - created))}, ran ${formatDuration(Math.max(0, finished - started))}`}
              >
                <span className="flex w-44 shrink-0 items-center gap-1.5 truncate text-xs">
                  <span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">d{execution.depth}</span>
                  <span className="truncate group-hover:underline">{name}</span>
                </span>
                <span className="relative h-3.5 min-w-0 flex-1 rounded-sm bg-muted/50">
                  {started > created ? (
                    <span
                      className="absolute inset-y-[5px] rounded-full bg-muted-foreground/25"
                      style={{ left: pct(created), width: pct(started - created) }}
                    />
                  ) : null}
                  <span
                    className={`absolute inset-y-0 min-w-[3px] rounded-sm ${BAR_TONE[tone]}`}
                    style={{ left: pct(started), width: pct(Math.max(0, finished - started)) }}
                  />
                </span>
                <span className="w-14 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
                  {execution.duration_ms === null ? "–" : formatDuration(execution.duration_ms)}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
};

export const ExecutionTimeline: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";

  const [suppressedOnly, setSuppressedOnly] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: [
      ...workspaceScope(workspaceId),
      "automation",
      "executions",
      suppressedOnly,
    ],
    queryFn: () =>
      listExecutions(workspaceId, {
        limit: 200,
        ...(suppressedOnly ? { suppressed_only: true } : {}),
      }),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
    refetchInterval: timelinePollInterval,
  });

  const chains = useMemo(() => {
    const grouped = new Map<string, AutomationExecution[]>();

    (data?.items ?? []).forEach((execution) => {
      const existing = grouped.get(execution.correlation_id);
      if (existing) {
        existing.push(execution);
      } else {
        grouped.set(execution.correlation_id, [execution]);
      }
    });

    return Array.from(grouped.entries()).map(([correlationId, executions]) => {
      const ordered = executions
        .slice()
        .sort(
          (a, b) =>
            a.depth - b.depth ||
            Date.parse(a.created_at) - Date.parse(b.created_at),
        );

      return {
        correlationId,
        executions: ordered,
        suppressed: ordered.filter((execution) => execution.is_suppressed),
        startedAt: ordered[0]?.created_at ?? "",
        totalSpentMicros: ordered.reduce(
          (sum, execution) => sum + execution.spent_cost_micros,
          0,
        ),
        tone: chainTone(ordered),
        title: ordered[0]?.rule_name ?? (ordered[0] ? `Rule ${ordered[0].rule_id.slice(0, 8)}` : "Run"),
        // Phase 2: wall-clock, first start to last finish. Runs set off by one event run side by
        // side, so adding their durations overstated how long the chain took.
        durationMs: chainSpanMs(ordered),
      };
    });
  }, [data]);

  const summary = useMemo(() => summarise(data?.items ?? [], chains.length), [data, chains.length]);

  if (isLoading) {
    return (
      <div className="space-y-3" role="status" aria-label="Loading executions">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading executions…
        </div>
        {[0, 1, 2].map((index) => (
          <div key={index} className="h-14 animate-pulse rounded-lg border border-border bg-card" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        role="alert"
        className="m-4 rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive"
      >
        Execution history couldn&apos;t be loaded.
      </div>
    );
  }

  return (
    <div className="space-y-4 w-full min-w-0">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Run history</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Execution traces, grouped into causal chains: a chain is everything one triggering event set off.
          </p>
        </div>

        <button
          type="button"
          onClick={() => setSuppressedOnly((current) => !current)}
          aria-pressed={suppressedOnly}
          className={[
            "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-xs self-start sm:self-auto",
            suppressedOnly
              ? "border-amber-500 bg-amber-500/10 text-amber-700"
              : "border-border hover:bg-muted",
          ].join(" ")}
        >
          <Filter className="h-3.5 w-3.5" />
          Blocked only
        </button>
      </div>

      {summary.runs > 0 ? (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" aria-label={`The last ${summary.runs} runs`}>
          <Kpi label="Chains" value={summary.chains} />
          <Kpi label="Runs" value={summary.runs} hint={data?.has_more ? `latest ${summary.runs}` : undefined} />
          <Kpi
            label="Completed"
            value={`${summary.completedPct}%`}
            hint={`${summary.completed} of ${summary.runs}`}
            tone={summary.completedPct === 100 ? "ok" : undefined}
          />
          <Kpi label="Blocked" value={summary.blocked} hint="loops, depth, budget" tone={summary.blocked > 0 ? "warn" : undefined} />
          <Kpi label="Failed" value={summary.failed} tone={summary.failed > 0 ? "danger" : undefined} />
          <Kpi label="Median run" value={summary.medianMs === null ? "–" : formatDuration(summary.medianMs)} hint={summary.p95Ms === null ? undefined : `p95 ${formatDuration(summary.p95Ms)}`} />
        </dl>
      ) : null}

      {chains.length === 0 ? (
        <p className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
          {suppressedOnly
            ? "Nothing has been blocked. No rule loops or depth limits have been hit."
            : "No automation has run yet in this workspace."}
        </p>
      ) : (
        <ul className="space-y-3 w-full min-w-0">
          {chains.map((chain) => {
            const isOpen = expanded === chain.correlationId;
            const hasSuppression = chain.suppressed.length > 0;

            return (
              <li
                key={chain.correlationId}
                className={[
                  "rounded-lg border overflow-hidden",
                  hasSuppression
                    ? "border-amber-500/50 bg-amber-500/5"
                    : "border-border bg-card",
                ].join(" ")}
              >
                <button
                  type="button"
                  onClick={() =>
                    setExpanded(isOpen ? null : chain.correlationId)
                  }
                  aria-expanded={isOpen}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/40 transition-colors"
                >
                  <ChevronRight
                    className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${isOpen ? "rotate-90" : ""}`}
                    aria-hidden="true"
                  />
                  <StatusDot tone={chain.tone} />

                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                      <span className="truncate">{chain.title}</span>
                      <span className="shrink-0 text-xs font-normal text-muted-foreground">
                        {chain.executions.length}{" "}
                        {chain.executions.length === 1 ? "step" : "steps"}
                      </span>
                      {hasSuppression && (
                        <span className="shrink-0 rounded bg-amber-500/20 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">
                          {chain.suppressed.length} blocked
                        </span>
                      )}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {chain.startedAt
                        ? formatTimestamp(chain.startedAt)
                        : ""}{" "}
                      · chain {chain.correlationId.slice(0, 8)}
                    </span>
                  </span>

                  <DurationBadge ms={chain.durationMs} />
                  {chain.totalSpentMicros > 0 && (
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {formatMicros(chain.totalSpentMicros)}
                    </span>
                  )}
                </button>

                {isOpen && chain.executions.length > 1 && (
                  <ChainWaterfall executions={chain.executions} />
                )}
                {isOpen && (
                  <ol className="border-t border-border/60 px-4 py-3 space-y-2 overflow-x-auto">
                    {chain.executions.map((execution, index) => (
                      <ExecutionStep
                        key={execution.id}
                        execution={execution}
                        isLast={index === chain.executions.length - 1}
                      />
                    ))}
                  </ol>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {data?.has_more && (
        <p className="text-xs text-muted-foreground">
          Showing the most recent {data.limit} executions.
        </p>
      )}
    </div>
  );
};

interface ExecutionStepProps {
  readonly execution: AutomationExecution;
  readonly isLast: boolean;
}

const ExecutionStep: React.FC<ExecutionStepProps> = ({
  execution,
  isLast,
}) => {
  const [showNodes, setShowNodes] = useState(false);
  const presentation = EXECUTION_STATUS_PRESENTATION[execution.status] ?? {
    label: execution.status,
    tone: "muted" as const,
    explanation: "",
  };

  const counterpartRuleId =
    typeof execution.details.counterpart_rule_id === "string"
      ? execution.details.counterpart_rule_id
      : null;

  const priorExecutionId =
    typeof execution.details.prior_execution_id === "string"
      ? execution.details.prior_execution_id
      : null;

  const reason =
    typeof execution.details.reason === "string"
      ? execution.details.reason
      : null;

  return (
    <li className="relative pl-6 min-w-0" id={`execution-${execution.id}`}>
      {!isLast && (
        <span
          aria-hidden="true"
          className="absolute left-[7px] top-6 h-full w-px bg-border"
        />
      )}

      <span className="absolute left-0 top-1">
        <StatusIcon status={execution.status} />
      </span>

      <div
        className={`mb-3 rounded-md border p-3 ${TONE_CLASSES[presentation.tone]} min-w-0`}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-sm font-medium break-words">
            {execution.rule_name ?? `Rule ${execution.rule_id.slice(0, 8)}`}
          </span>
          <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
            depth {execution.depth}
            <DurationBadge ms={execution.duration_ms} />
          </span>
        </div>

        <p className="mt-0.5 text-xs font-medium">{presentation.label}</p>

        {presentation.explanation && (
          <p className="mt-1 text-xs text-muted-foreground">
            {presentation.explanation}
          </p>
        )}

        {execution.status === "SUPPRESSED_CYCLE" && counterpartRuleId && (
          <p className="mt-1.5 rounded bg-background/60 px-2 py-1 text-xs break-all">
            Triggered by rule{" "}
            <span className="font-mono">{counterpartRuleId.slice(0, 8)}</span>,
            which this rule also triggers. Change one of the two to break the
            loop.
          </p>
        )}

        {reason && !presentation.explanation.includes(reason) && (
          <p className="mt-1 text-xs text-muted-foreground break-words">{reason}</p>
        )}

        {execution.error && (
          <p className="mt-1.5 break-all rounded bg-background/60 px-2 py-1 font-mono text-[11px] text-destructive">
            {execution.error}
          </p>
        )}

        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          {execution.node_count > 0 && (
            <span>
              {execution.nodes_executed}/{execution.node_count} nodes
            </span>
          )}
          {execution.actions_executed > 0 && (
            <span>{execution.actions_executed} actions</span>
          )}
          {execution.spent_cost_micros > 0 && (
            <span>
              {formatMicros(execution.spent_cost_micros)} of{" "}
              {formatMicros(execution.budget_cost_micros)}
            </span>
          )}
          {execution.emitted_event_ids.length > 0 && (
            <span>
              emitted {execution.emitted_event_ids.length}{" "}
              {execution.emitted_event_ids.length === 1 ? "event" : "events"}
            </span>
          )}
          {priorExecutionId && (
            <span className="font-mono break-all">
              prior {priorExecutionId.slice(0, 8)}
            </span>
          )}
          {execution.node_count > 0 && (
            <button
              type="button"
              onClick={() => setShowNodes((open) => !open)}
              aria-expanded={showNodes}
              className="font-semibold text-primary hover:underline"
            >
              {showNodes ? "Hide steps" : "Show steps"}
            </button>
          )}
        </div>

        {showNodes && <NodeRuns executionId={execution.id} />}
      </div>
    </li>
  );
};

/**
 * ARCH37-S2:node-runs. What each node did — the action type, what it created (delivery,
 * job, verification) and how long it took.
 */
const NodeRuns: React.FC<{ readonly executionId: string }> = ({ executionId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { data, isLoading, isError } = useQuery({
    queryKey: automationKeys.executionNodes(workspaceId, executionId),
    queryFn: () => listExecutionNodes(workspaceId, executionId),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
  });
  if (isLoading) {
    return <p className="mt-2 text-[11px] text-muted-foreground">Loading steps…</p>;
  }
  if (isError || !data) {
    return <p className="mt-2 text-[11px] text-destructive">Steps could not be loaded.</p>;
  }
  return (
    <ol className="relative mt-2 space-y-1.5 rounded-md border border-border/60 bg-background/60 p-2 pl-3 text-[11px]" aria-label="Steps of this run">
      {data.map((node, index) => (
        <li key={node.id} className="relative flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-4">
          {index < data.length - 1 && (
            <span aria-hidden="true" className="absolute left-[4px] top-3.5 h-[calc(100%+0.375rem)] w-px bg-border" />
          )}
          <span className="absolute left-0 top-1">
            <StatusDot tone={nodeTone(node.status)} label={node.status.toLowerCase()} />
          </span>
          <span className="font-mono text-muted-foreground">#{node.sequence}</span>
          <span className="font-semibold">{node.action_type ?? node.node_type ?? node.node_key}</span>
          <span className={node.status === "FAILED" ? "text-destructive" : "text-muted-foreground"}>
            {node.status.toLowerCase()}
          </span>
          <DurationBadge ms={node.duration_ms} />
          {node.external_ref && (
            <span className="font-mono text-muted-foreground break-all" title="Reference to what this step created">
              ref {node.external_ref.slice(0, 12)}
            </span>
          )}
          {node.outcome && <span className="text-muted-foreground break-words">— {node.outcome}</span>}
          {node.error && <span className="w-full break-all font-mono text-destructive">{node.error}</span>}
        </li>
      ))}
    </ol>
  );
};

export default ExecutionTimeline;
