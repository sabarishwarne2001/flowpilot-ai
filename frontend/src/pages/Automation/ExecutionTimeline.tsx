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
import { workspaceScope } from "@/services/api/queryKeys";
import { formatMicros } from "@/types/billing";

const TONE_CLASSES: Record<string, string> = {
  ok: "border-emerald-500/40 bg-emerald-500/5",
  warn: "border-amber-500/50 bg-amber-500/10",
  danger: "border-destructive/50 bg-destructive/10",
  muted: "border-border bg-card",
};

const StatusIcon: React.FC<{ status: AutomationExecutionStatus }> = ({
  status,
}) => {
  switch (status) {
    case "COMPLETED":
      return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
    case "FAILED":
    case "TIMED_OUT":
      return <XCircle className="h-4 w-4 text-destructive" />;
    case "SUPPRESSED_CYCLE":
      return <RotateCcw className="h-4 w-4 text-amber-600" />;
    case "SUPPRESSED_DEPTH":
      return <ArrowDown className="h-4 w-4 text-amber-600" />;
    case "BUDGET_EXHAUSTED":
      return <Ban className="h-4 w-4 text-amber-600" />;
    default:
      return <Clock className="h-4 w-4 text-muted-foreground" />;
  }
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
    refetchInterval: 30_000,
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
      };
    });
  }, [data]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading executions…
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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Execution traces</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Grouped into causal chains. A chain is everything one triggering
            event set off.
          </p>
        </div>

        <button
          type="button"
          onClick={() => setSuppressedOnly((current) => !current)}
          aria-pressed={suppressedOnly}
          className={[
            "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs",
            suppressedOnly
              ? "border-amber-500 bg-amber-500/10 text-amber-700"
              : "border-border hover:bg-muted",
          ].join(" ")}
        >
          <Filter className="h-3.5 w-3.5" />
          Blocked only
        </button>
      </div>

      {chains.length === 0 ? (
        <p className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
          {suppressedOnly
            ? "Nothing has been blocked. No rule loops or depth limits have been hit."
            : "No automation has run yet in this workspace."}
        </p>
      ) : (
        <ul className="space-y-3">
          {chains.map((chain) => {
            const isOpen = expanded === chain.correlationId;
            const hasSuppression = chain.suppressed.length > 0;

            return (
              <li
                key={chain.correlationId}
                className={[
                  "rounded-lg border",
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
                  className="flex w-full items-center gap-3 px-4 py-3 text-left"
                >
                  <ChevronRight
                    className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${isOpen ? "rotate-90" : ""}`}
                    aria-hidden="true"
                  />

                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium">
                      {chain.executions.length}{" "}
                      {chain.executions.length === 1 ? "step" : "steps"}
                      {hasSuppression && (
                        <span className="ml-2 rounded bg-amber-500/20 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">
                          {chain.suppressed.length} blocked
                        </span>
                      )}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {chain.startedAt
                        ? new Date(chain.startedAt).toLocaleString()
                        : ""}{" "}
                      · chain {chain.correlationId.slice(0, 8)}
                    </span>
                  </span>

                  {chain.totalSpentMicros > 0 && (
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {formatMicros(chain.totalSpentMicros)}
                    </span>
                  )}
                </button>

                {isOpen && (
                  <ol className="border-t border-border/60 px-4 py-3">
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
    <li className="relative pl-6">
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
        className={`mb-3 rounded-md border p-2.5 ${TONE_CLASSES[presentation.tone]}`}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-sm font-medium">
            {execution.rule_name ?? `Rule ${execution.rule_id.slice(0, 8)}`}
          </span>
          <span className="text-xs text-muted-foreground">
            depth {execution.depth}
            {execution.duration_ms !== null && ` · ${execution.duration_ms}ms`}
          </span>
        </div>

        <p className="mt-0.5 text-xs font-medium">{presentation.label}</p>

        {presentation.explanation && (
          <p className="mt-1 text-xs text-muted-foreground">
            {presentation.explanation}
          </p>
        )}

        {execution.status === "SUPPRESSED_CYCLE" && counterpartRuleId && (
          <p className="mt-1.5 rounded bg-background/60 px-2 py-1 text-xs">
            Triggered by rule{" "}
            <span className="font-mono">{counterpartRuleId.slice(0, 8)}</span>,
            which this rule also triggers. Change one of the two to break the
            loop.
          </p>
        )}

        {reason && !presentation.explanation.includes(reason) && (
          <p className="mt-1 text-xs text-muted-foreground">{reason}</p>
        )}

        {execution.error && (
          <p className="mt-1.5 break-words rounded bg-background/60 px-2 py-1 font-mono text-[11px] text-destructive">
            {execution.error}
          </p>
        )}

        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
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
            <span className="font-mono">
              prior {priorExecutionId.slice(0, 8)}
            </span>
          )}
        </div>
      </div>
    </li>
  );
};

export default ExecutionTimeline;
