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
 *
 * Additional providers can be added later without changing
 * the surrounding DTO structure.
 */
export type AutomationActionType =
  | "SEND_EMAIL";

/**
 * Execution status of an automation log.
 */
export type AutomationExecutionStatus =
  | "SUCCESS"
  | "FAILED";

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

  readonly event: AutomationEvent;

  readonly field: string;

  readonly operator: AutomationOperator;

  readonly value: string;

  readonly action_type: AutomationActionType;

  /**
   * Provider-specific configuration.
   *
   * Example:
   * {
   *   recipient: "...",
   *   subject: "...",
   * }
   */
  readonly action_config: Record<string, unknown>;

  readonly is_active: boolean;

  readonly created_at: string;
  readonly updated_at: string;
}

/**
 * Payload used when creating a rule.
 */
export interface AutomationRuleCreateRequest {
  readonly name: string;

  readonly event: AutomationEvent;

  readonly field: string;

  readonly operator: AutomationOperator;

  readonly value: string;

  readonly action_type: AutomationActionType;

  readonly action_config: Record<string, unknown>;

  readonly is_active?: boolean;
}

/**
 * Partial payload used when updating a rule.
 */
export interface AutomationRuleUpdateRequest {
  readonly name?: string;

  readonly event?: AutomationEvent;

  readonly field?: string;

  readonly operator?: AutomationOperator;

  readonly value?: string;

  readonly action_type?: AutomationActionType;

  readonly action_config?: Record<string, unknown>;

  readonly is_active?: boolean;
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
