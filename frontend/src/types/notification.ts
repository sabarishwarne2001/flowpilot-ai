/**
 * In-App Notification Center Data Transfer Objects (DTOs) for FlowPilot AI.
 *
 * Defines the immutable TypeScript compilation interfaces mirroring backend Pydantic
 * schemas exactly, providing consistent state definitions for real-time alerting dashboards.
 */

/* =========================================================================
   Notification DTOs
   ========================================================================= */

/**
 * Serialized in-app alert card representation returned by the database (NotificationResponse).
 */
export interface Notification {
  readonly id: string;
  readonly user_id: string;
  /**
   * Title header describing the system or pipeline event (e.g. "Automation Matched").
   */
  readonly title: string;
  /**
   * Description message containing performance metrics, results, or trace errors.
   */
  readonly message: string;
  readonly is_read: boolean;
  /**
   * Optional primary key UUID pointing to the source document context.
   */
  readonly work_item_id: string | null;
  readonly created_at: string; // UTC ISO-8601 timestamp string
  readonly updated_at: string; // UTC ISO-8601 timestamp string
}

/* =========================================================================
   Request DTOs
   ========================================================================= */

/**
 * Ingestion parameters used to modify the read/unread status of an alert card.
 */
export interface NotificationUpdateRequest {
  readonly is_read: boolean;
}

/* =========================================================================
   Response DTOs
   ========================================================================= */

/**
 * Response payload returned upon successful bulk "Mark all as read" API transactions.
 */
export interface MarkAllReadResponse {
  readonly updated_count: number;
}
