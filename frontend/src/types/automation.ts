/**
 * Automation Engine Data Transfer Objects (DTOs) for FlowPilot AI.
 */

export type AutomationEvent =
  | "WORK_ITEM_CREATED"
  | "WORK_ITEM_COMPLETED"
  | "WORK_ITEM_FAILED"
  | "WORK_ITEM_REPROCESSED";

export type AutomationOperator =
  | "EQUALS"
  | "NOT_EQUALS"
  | "CONTAINS"
  | "NOT_CONTAINS"
  | "STARTS_WITH"
  | "ENDS_WITH"
  | "GREATER_THAN"
  | "LESS_THAN"
  | "GREATER_THAN_OR_EQUAL"
  | "LESS_THAN_OR_EQUAL"
  | "BETWEEN"
  | "IN"
  | "NOT_IN"
  | "EXISTS"
  | "IS_EMPTY"
  | "IS_NOT_EMPTY"
  | "ARRAY_CONTAINS_ANY"
  | "ARRAY_CONTAINS_ALL";

export type AutomationActionType = "SEND_EMAIL";
export type AutomationExecutionStatus = "SUCCESS" | "FAILED";
export type AutomationLogicOperator = "AND" | "OR";

export interface AutomationCondition {
  readonly field: string;
  readonly operator: AutomationOperator;
  readonly value: string;
}

export type AutomationErrorPolicy = "HALT" | "CONTINUE";

export interface AutomationAction {
  readonly action_type: string;
  readonly config: Record<string, unknown>;
}

export interface AutomationRule {
  readonly id: string;
  readonly workspace_id?: string;
  readonly created_by_user_id?: string | null;
  readonly name: string;
  readonly priority: number;
  readonly event: AutomationEvent;
  readonly conditions: readonly AutomationCondition[];
  readonly logic_operator: AutomationLogicOperator;
  readonly actions: readonly AutomationAction[];
  readonly is_active: boolean;
  readonly created_at: string;
  readonly updated_at: string;
  readonly graph_version?: number;
  readonly on_error?: AutomationErrorPolicy;
}

export interface AutomationRuleCreateRequest {
  readonly name: string;
  readonly priority: number;
  readonly event: AutomationEvent;
  readonly conditions: readonly AutomationCondition[];
  readonly logic_operator: AutomationLogicOperator;
  readonly actions: readonly AutomationAction[];
  readonly is_active?: boolean;
}

export interface AutomationRuleUpdateRequest {
  readonly name?: string;
  readonly priority?: number;
  readonly event?: AutomationEvent;
  readonly conditions?: readonly AutomationCondition[];
  readonly logic_operator?: AutomationLogicOperator;
  readonly actions?: readonly AutomationAction[];
  readonly is_active?: boolean;
}

export interface AutomationLog {
  readonly id: string;
  readonly rule_id: string;
  readonly work_item_id: string;
  readonly rule_name: string;
  readonly document_name: string;
  readonly action_type: string;
  readonly status: "SUCCESS" | "FAILED";
  readonly log_message: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly graph_version?: number;
  readonly on_error?: AutomationErrorPolicy;

  readonly execution_status?: string | null;
  readonly execution_time_ms?: number | null;
  readonly spent_cost_micros?: number | null;
  readonly nodes_executed?: number | null;
  readonly actions_executed?: number | null;
}

export const formatCostMicros = (micros: number | null | undefined): string => {
  if (micros === null || micros === undefined) {
    return "—";
  }
  const dollars = micros / 1_000_000;
  const decimals = dollars !== 0 && Math.abs(dollars) < 0.01 ? 4 : 2;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(dollars);
};

export const formatDurationMs = (ms: number | null | undefined): string => {
  if (ms === null || ms === undefined) {
    return "—";
  }
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
};

export interface AutomationRuleTestResponse {
  readonly success: boolean;
  readonly matched: boolean;
  readonly notification_sent: boolean;
  readonly message: string;
  readonly execution_time_ms: number;
}
