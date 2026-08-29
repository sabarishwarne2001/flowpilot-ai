/**
 * Mirrors app/core/webhook_events.py::WEBHOOK_EVENT_TYPES.
 */
export const WEBHOOK_EVENT_TYPES = [
  "organization.updated",
  "member.invited",
  "member.joined",
  "member.role_changed",
  "member.deactivated",
  "member.reactivated",
  "invitation.created",
  "invitation.accepted",
  "invitation.rejected",
  "invitation.revoked",
  "invitation.expired",
  "workspace.created",
  "workspace.updated",
  "workspace.archived",
  "workspace.restored",
  "work_item.created",
  "work_item.updated",
  "work_item.deleted",
  "document.queued",
  "document.processing",
  "document.completed",
  "document.failed",
] as const;

export type WebhookEventType = (typeof WEBHOOK_EVENT_TYPES)[number];

export interface WebhookEndpoint {
  readonly id: string;
  readonly organization_id: string;
  readonly url: string;
  readonly description: string | null;
  readonly event_types: readonly string[];
  readonly status: string;
  readonly auto_disabled: boolean;
  readonly disabled_at: string | null;
  readonly disabled_reason: string | null;
  readonly consecutive_failures: number;
  readonly last_success_at: string | null;
  readonly last_failure_at: string | null;
  readonly secret_last_rotated_at: string;
  /** While set, the previous signing secret is still accepted. */
  readonly rotation_overlap_until: string | null;
  readonly created_at: string;
}

/** POST /endpoints only. The secret is never retrievable again. */
export interface WebhookEndpointCreated {
  readonly endpoint: WebhookEndpoint;
  readonly secret: string;
}

export interface WebhookRotateSecretResult {
  readonly secret: string;
  readonly previous_secret_valid_until: string;
  readonly note: string;
}

export interface WebhookDelivery {
  readonly id: string;
  readonly webhook_endpoint_id: string;
  readonly event_type: string;
  readonly status: string;
  readonly attempts: number;
  readonly available_at: string;
  readonly delivered_at: string | null;
  readonly last_response_status: number | null;
  readonly last_error: string | null;
  readonly created_at: string;
}

export interface WebhookAttempt {
  readonly id: string;
  readonly attempt_number: number;
  readonly disposition: string;
  readonly response_status: number | null;
  readonly duration_ms: number;
  readonly error: string | null;
  readonly resolved_ip: string | null;
  readonly attempted_at: string;
  readonly response_body_excerpt: string | null;
}

export interface WebhookEndpointCreateRequest {
  readonly url: string;
  readonly event_types: readonly string[];
  readonly description?: string | null;
}

export interface WebhookEndpointUpdateRequest {
  readonly url?: string;
  readonly event_types?: readonly string[];
  readonly description?: string | null;
  readonly status?: "ACTIVE" | "DISABLED";
}
