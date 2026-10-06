/**
 * In-App Notification Center Data Transfer Objects (DTOs) for FlowPilot AI.
 */

/** The server's `NotificationType`: the categories the inbox filters by. */
export const NOTIFICATION_CATEGORIES = ["DOCUMENT", "AUTOMATION", "EMAIL", "SYSTEM", "SECURITY"] as const;
export type NotificationCategory = (typeof NOTIFICATION_CATEGORIES)[number];

export const NOTIFICATION_CATEGORY_LABELS: Record<NotificationCategory, string> = {
  DOCUMENT: "Documents",
  AUTOMATION: "Automation",
  EMAIL: "Email",
  SYSTEM: "System",
  SECURITY: "Security",
};

export interface Notification {
  readonly id: string;
  readonly user_id: string;
  readonly title: string;
  readonly message: string;
  readonly is_read: boolean;
  readonly notification_type?: string;
  readonly priority?: string;
  readonly work_item_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface NotificationPage {
  readonly items: readonly Notification[];
  readonly total: number;
  readonly unread_count: number;
  readonly limit: number;
  readonly offset: number;
}

export interface NotificationUpdateRequest {
  readonly is_read: boolean;
}

export interface MarkAllReadResponse {
  readonly updated_count: number;
}
