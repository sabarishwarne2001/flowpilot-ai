/**
 * Dashboard Data Transfer Objects (DTOs) for FlowPilot AI.
 *
 * These interfaces represent dashboard-specific analytics returned
 * by the backend. They intentionally remain independent from the
 * WorkItem DTOs to preserve domain isolation.
 */

/* --------------------------------------------------------------------------
 * Shared Types
 * -------------------------------------------------------------------------- */

/**
 * Supported dashboard activity events.
 */
export type DashboardEventType =
  | "UPLOAD_COMPLETED"
  | "PROCESS_STARTED"
  | "PROCESS_COMPLETED"
  | "PROCESS_FAILED"
  | "AUTOMATION_TRIGGERED";

/**
 * Processing status metrics.
 *
 * Designed for future expansion (queue metrics, worker metrics,
 * WebSocket updates, etc.).
 */
export interface DashboardProcessingStatus {
  readonly queued: number;
  readonly processing: number;
  readonly total: number;
}

/**
 * Document type distribution used by dashboard charts.
 */
export interface DashboardDocTypeDistribution {
  readonly document_type: string;
  readonly count: number;
  readonly percentage: number;
}

/**
 * Recent activity feed item.
 *
 * Timestamp is represented as a UTC ISO-8601 string.
 */
export interface DashboardActivityFeed {
  readonly id: string;

  readonly event_type: DashboardEventType;

  readonly description: string;

  readonly timestamp: string;

  /**
   * Optional work item associated with this activity.
   *
   * Present for events that originate from a document
   * processing pipeline (such as PROCESS_FAILED) and
   * used to enable retry operations from the dashboard.
   */
  readonly work_item_id: string | null;
}
/**
 * Dashboard KPI metrics returned by the backend.
 */
export interface DashboardMetricsResponse {
  /**
   * Total documents belonging to the authenticated user.
   */
  readonly total_work_items: number;

  /**
   * Documents successfully processed today.
   */
  readonly processed_today: number;

  /**
   * Current processing queue statistics.
   */
  readonly processing_status: DashboardProcessingStatus;

  /**
   * Total failed processing jobs.
   */
  readonly failed_count: number;

  /**
   * Automation success rate expressed
   * as a percentage between 0 and 100.
   */
  readonly automation_success_rate: number;

  /**
   * Distribution of uploaded document types.
   */
  readonly document_type_distribution: readonly DashboardDocTypeDistribution[];

  /**
   * Recent dashboard activity.
   */
  readonly recent_activity: readonly DashboardActivityFeed[];
}
/**
 * Future dashboard extensions.
 *
 * Reserved for dashboard-specific analytics and summary
 * DTOs as the platform grows (charts, trends, forecasts,
 * real-time monitoring, etc.).
 *
 * Keeping this section separate preserves domain isolation
 * and minimizes future breaking changes.
 */
