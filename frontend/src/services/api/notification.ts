import apiClient from "@/services/api/client";
import {
  NOTIFICATION_ENDPOINTS,
  ORG_NOTIFICATION_ENDPOINTS,
} from "@/services/api/endpoints";
import type {
  Notification,
  NotificationPage,
  NotificationUpdateRequest,
  MarkAllReadResponse,
} from "@/types/notification";

export const getNotifications = async (
  workspaceId: string,
  isRead?: boolean | undefined,
): Promise<readonly Notification[]> => {
  const queryParams = new URLSearchParams();

  if (isRead !== undefined) {
    queryParams.append("is_read", isRead.toString());
  }

  const response = await apiClient.get<readonly Notification[]>(
    NOTIFICATION_ENDPOINTS.list(workspaceId),
    {
      params: queryParams,
      headers: {
        Accept: "application/json",
      },
    },
  );
  return response.data;
};

export const updateNotificationRead = async (
  workspaceId: string,
  notificationId: string,
  isRead: boolean,
): Promise<Notification> => {
  const payload: NotificationUpdateRequest = {
    is_read: isRead,
  };

  const response = await apiClient.patch<Notification>(
    NOTIFICATION_ENDPOINTS.detail(workspaceId, notificationId),
    payload,
    {
      headers: {
        Accept: "application/json",
      },
    },
  );
  return response.data;
};

export const markAllNotificationsRead = async (
  workspaceId: string,
): Promise<MarkAllReadResponse> => {
  const response = await apiClient.post<MarkAllReadResponse>(
    NOTIFICATION_ENDPOINTS.markAllRead(workspaceId),
    null,
    {
      headers: {
        Accept: "application/json",
      },
    },
  );
  return response.data;
};

export const deleteNotification = async (
  workspaceId: string,
  notificationId: string,
): Promise<void> => {
  await apiClient.delete(
    NOTIFICATION_ENDPOINTS.detail(workspaceId, notificationId),
    {
      headers: {
        Accept: "application/json",
      },
    },
  );
};

export const getOrganizationNotifications = async (
  organizationId: string,
  params: {
    isRead?: boolean | undefined;
    limit?: number | undefined;
    offset?: number | undefined;
  } = {},
): Promise<NotificationPage> => {
  const response = await apiClient.get<NotificationPage>(
    ORG_NOTIFICATION_ENDPOINTS.list(organizationId),
    {
      params: {
        ...(params.isRead !== undefined ? { is_read: params.isRead } : {}),
        limit: params.limit ?? 25,
        offset: params.offset ?? 0,
      },
    },
  );
  return response.data;
};

export const notificationApi = {
  getNotifications,
  updateNotificationRead,
  markAllNotificationsRead,
  deleteNotification,
  getOrganizationNotifications,
};

export default notificationApi;
