import apiClient from "@/services/api/client";
import type {
  Notification,
  NotificationUpdateRequest,
  MarkAllReadResponse,
} from "@/types/notification";

/**
 * Notifications API Route Path Mappings.
 */
const NOTIFICATION_ENDPOINTS = {
  BASE: "/notifications",
  DETAIL: (id: string) => `/notifications/${id}`,
  MARK_ALL_READ: "/notifications/mark-all-read",
} as const;

/**
 * Retrieves a list of in-app alert Notification cards belonging to the active user.
 *
 * Supports optional parameters filtering to isolate read or unread alerts.
 *
 * @param isRead - Optional filter parameter to select only read or unread alert states.
 * @returns Immutable chronological list of alert objects.
 */
export const getNotifications = async (
  isRead?: boolean,
): Promise<readonly Notification[]> => {
  const queryParams = new URLSearchParams();

  if (isRead !== undefined) {
    queryParams.append("is_read", isRead.toString());
  }

  const response = await apiClient.get<readonly Notification[]>(
    NOTIFICATION_ENDPOINTS.BASE,
    {
      params: queryParams,
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

/**
 * Modifies the read/unread state of a specific in-app Notification card by UUID.
 *
 * @param notificationId - Primary key UUID identifying the target alert.
 * @param isRead - Target status flag.
 * @returns The serialized updated Notification parameters.
 */
export const updateNotificationRead = async (
  notificationId: string,
  isRead: boolean,
): Promise<Notification> => {
  const payload: NotificationUpdateRequest = {
    is_read: isRead,
  };

  const response = await apiClient.patch<Notification>(
    NOTIFICATION_ENDPOINTS.DETAIL(notificationId),
    payload,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

/**
 * Executes a bulk, one-click modification to transition all unread cards to read.
 *
 * @returns Response mapping total updated counts.
 */
export const markAllNotificationsRead = async (): Promise<MarkAllReadResponse> => {
  const response = await apiClient.post<MarkAllReadResponse>(
    NOTIFICATION_ENDPOINTS.MARK_ALL_READ,
    null,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

/**
 * Removes a specific in-app Notification card from PostgreSQL.
 *
 * @param notificationId - Primary key UUID identifying the target alert.
 */
export const deleteNotification = async (notificationId: string): Promise<void> => {
  await apiClient.delete(NOTIFICATION_ENDPOINTS.DETAIL(notificationId), {
    headers: {
      "Accept": "application/json",
    },
  });
};

// Export unified API namespace wrapper
export const notificationApi = {
  getNotifications,
  updateNotificationRead,
  markAllNotificationsRead,
  deleteNotification,
};

export default notificationApi;
