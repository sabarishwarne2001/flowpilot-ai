/**
 * Automation Engine Data Transfer Objects (DTOs) for FlowPilot AI.
 *
 * Mirrors the backend Pydantic models while remaining provider-agnostic and
 * extensible for future automation channels and workflow engines.
 */

/* ============================================================================
 * Enums / Union Types
 * ========================================================================== */

/**
 * Events capable of triggering an automation rule.
 */
export type AutomationEvent =
  | "WORK_ITEM_CREATED"
  | "WORK_ITEM_COMPLETED"
  | "WORK_ITEM_FAILED"
  | "WORK_ITEM_REPROCESSED";

/**
 * Supported comparison operators.
 */
export type AutomationOperator =
  | "EQUALS"
  | "NOT_EQUALS"
  | "CONTAINS"
  | "GREATER_THAN"
  | "LESS_THAN"
  | "GREATER_THAN_OR_EQUAL"
  | "LESS_THAN_OR_EQUAL";

/**
 * Supported automation actions.
 */
export type AutomationActionType =
  | "SEND_EMAIL";

/**
 * Execution status of an automation log.
 */
export type AutomationExecutionStatus =
  | "SUCCESS"
  | "FAILED";

/**
 * Logical operators supporting multiple conditions.
 */
export type AutomationLogicOperator = "AND" | "OR";

/* ============================================================================
 * Sub-Structures
 * ========================================================================== */

/**
 * Single logical evaluate criteria constraint configured for rule matching.
 */
export interface AutomationCondition {
  readonly field: string;
  readonly operator: AutomationOperator;
  readonly value: string;
}

/**
 * Single trigger action workflow configured for rule matching executions.
 */
export interface AutomationAction {
  readonly action_type: string;
  readonly config: Record<string, unknown>;
}

/* ============================================================================
 * Rule DTOs
 * ========================================================================== */

/**
 * Persisted automation rule returned by the backend.
 */
export interface AutomationRule {
  readonly id: string;
  readonly user_id: string;

  readonly name: string;
  readonly priority: number;

  readonly event: AutomationEvent;

  readonly conditions: readonly AutomationCondition[];
  readonly logic_operator: AutomationLogicOperator;

  readonly actions: readonly AutomationAction[];

  readonly is_active: boolean;

  readonly created_at: string;
  readonly updated_at: string;
}

/**
 * Payload used when creating a rule.
 */
export interface AutomationRuleCreateRequest {
  readonly name: string;
  readonly priority: number;

  readonly event: AutomationEvent;

  readonly conditions: readonly AutomationCondition[];
  readonly logic_operator: AutomationLogicOperator;

  readonly actions: readonly AutomationAction[];

  readonly is_active?: boolean;
}

/**
 * Partial payload used when updating a rule.
 */
export interface AutomationRuleUpdateRequest {
  readonly name?: string;
  readonly priority?: number;

  readonly event?: AutomationEvent;

  readonly conditions?: readonly AutomationCondition[];
  readonly logic_operator?: AutomationLogicOperator;

  readonly actions?: readonly AutomationAction[];

  readonly is_active?: boolean;
}

/**
 * Result of running a "test" evaluation of a rule against a sample work item,
 * without persisting an execution log.
 */
export interface AutomationRuleTestResponse {
  readonly success: boolean;
  readonly matched: boolean;
  readonly notification_sent: boolean;
  readonly message: string;
  readonly execution_time_ms: number;
}

/* ============================================================================
 * Automation Logs
 * ========================================================================== */

/**
 * Historical automation execution record.
 */
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
}
