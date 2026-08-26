/**
 * ARCH-13 execution traces — the causal view.
 */

import apiClient from "@/services/api/client";

export type AutomationExecutionStatus =
  | "QUEUED"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "TIMED_OUT"
  | "SUPPRESSED_CYCLE"
  | "SUPPRESSED_DEPTH"
  | "BUDGET_EXHAUSTED";

export interface AutomationExecution {
  readonly id: string;
  readonly organization_id: string;
  readonly workspace_id: string;
  readonly rule_id: string;
  readonly rule_name: string | null;
  readonly work_item_id: string | null;
  readonly outbox_event_id: string | null;
  readonly correlation_id: string;
  readonly depth: number;
  readonly status: AutomationExecutionStatus;

  readonly started_at: string | null;
  readonly completed_at: string | null;
  readonly deadline_at: string | null;
  readonly created_at: string;

  readonly budget_cost_micros: number;
  readonly spent_cost_micros: number;

  readonly node_count: number;
  readonly nodes_executed: number;
  readonly actions_executed: number;

  readonly emitted_event_ids: readonly string[];
  readonly error: string | null;
  readonly details: Record<string, unknown>;
  readonly is_suppressed: boolean;
  readonly duration_ms: number | null;
}

export interface AutomationExecutionPage {
  readonly items: readonly AutomationExecution[];
  readonly limit: number;
  readonly has_more: boolean;
  readonly next_offset: number | null;
}

export interface ExecutionQuery {
  readonly correlation_id?: string;
  readonly rule_id?: string;
  readonly work_item_id?: string;
  readonly status?: AutomationExecutionStatus;
  readonly suppressed_only?: boolean;
  readonly offset?: number;
  readonly limit?: number;
}

const ws = (workspaceId: string): string => {
  if (!workspaceId) {
    throw new Error(
      "A workspaceId is required. Gate the query with `enabled: Boolean(workspaceId)`.",
    );
  }
  return encodeURIComponent(workspaceId);
};

export const EXECUTION_ENDPOINTS = {
  list: (workspaceId: string) =>
    `/workspaces/${ws(workspaceId)}/automation/executions`,
  detail: (workspaceId: string, executionId: string) =>
    `/workspaces/${ws(workspaceId)}/automation/executions/${encodeURIComponent(executionId)}`,
} as const;

export const listExecutions = async (
  workspaceId: string,
  query: ExecutionQuery = {},
): Promise<AutomationExecutionPage> => {
  const params: Record<string, string | number | boolean> = {};
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      params[key] = value as string | number | boolean;
    }
  });

  const response = await apiClient.get<AutomationExecutionPage>(
    EXECUTION_ENDPOINTS.list(workspaceId),
    { params },
  );
  return response.data;
};

export const getExecution = async (
  workspaceId: string,
  executionId: string,
): Promise<AutomationExecution> => {
  const response = await apiClient.get<AutomationExecution>(
    EXECUTION_ENDPOINTS.detail(workspaceId, executionId),
  );
  return response.data;
};

export interface StatusPresentation {
  readonly label: string;
  readonly tone: "ok" | "warn" | "danger" | "muted";
  readonly explanation: string;
}

export const EXECUTION_STATUS_PRESENTATION: Record<
  AutomationExecutionStatus,
  StatusPresentation
> = {
  QUEUED: {
    label: "Queued",
    tone: "muted",
    explanation: "Waiting for a worker.",
  },
  RUNNING: {
    label: "Running",
    tone: "muted",
    explanation: "In progress.",
  },
  COMPLETED: {
    label: "Completed",
    tone: "ok",
    explanation: "All actions ran.",
  },
  FAILED: {
    label: "Failed",
    tone: "danger",
    explanation: "An action raised an error. See the detail below.",
  },
  TIMED_OUT: {
    label: "Timed out",
    tone: "danger",
    explanation: "The execution passed its deadline and was abandoned.",
  },
  SUPPRESSED_CYCLE: {
    label: "Blocked — rule loop",
    tone: "warn",
    explanation:
      "This rule had already run in this chain, so it was refused. Two rules are triggering each other.",
  },
  SUPPRESSED_DEPTH: {
    label: "Blocked — chain too deep",
    tone: "warn",
    explanation:
      "The chain of distinct rules exceeded the depth limit. This is not a loop — the chain is simply long.",
  },
  BUDGET_EXHAUSTED: {
    label: "Stopped — budget spent",
    tone: "warn",
    explanation:
      "The execution reached its cost ceiling partway through and stopped.",
  },
};

export const executionApi = {
  listExecutions,
  getExecution,
} as const;

export default executionApi;
