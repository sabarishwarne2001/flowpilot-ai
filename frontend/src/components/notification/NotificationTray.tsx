import React, { useEffect, useCallback, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  Bell,
  CheckCheck,
  Trash2,
  Loader2,
  AlertCircle,
  X,
  FileText,
  Clock,
} from "lucide-react";
import { notificationApi } from "@/services/api/notification";
import { useUIStore } from "@/store/useUIStore";
import { formatDateTime } from "@/utils/formatters";
import { ApiError } from "@/services/api/client";
import { ROUTES } from "@/constants/routes";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import type { Notification } from "@/types/notification";

// Centralized query key constant matching our verified workspace standards
const NOTIFICATIONS_QUERY_KEY = ["notifications"] as const;

interface NotificationTrayProps {
  /**
   * Tracks whether the floating popover tray is visually open in the workspace header.
   */
  readonly isOpen: boolean;
  /**
   * Toggle callback to handle closures during background backdrop clicks.
   */
  readonly onClose: () => void;
  readonly className?: string;
}

/**
 * Absolute, floating Notification Center alert cards tray popover for FlowPilot AI.
 *
 * Intercepts unread indices, triggers synchronized Zustand top-bell badge updates,
 * implements bulk "Mark all read" states, and links clicks to document details sheets.
 */
export const NotificationTray: React.FC<NotificationTrayProps> = ({
  isOpen,
  onClose,
  className = "",
}) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [notificationToDelete, setNotificationToDelete] =
    useState<Notification | null>(null);
  const setNotificationBadgeCount = useUIStore(
    (state) => state.setNotificationBadgeCount
  );

  // 1. Query raw alerts data from database
  const {
    data: notifications = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: NOTIFICATIONS_QUERY_KEY,
    queryFn: () => notificationApi.getNotifications(),

    // Always keep notifications synchronized.
    staleTime: 5_000,

    // Poll every 5 seconds.
    refetchInterval: 5_000,

    // Refresh when user returns to the tab.
    refetchOnWindowFocus: true,

    // Refresh after reconnecting.
    refetchOnReconnect: true,

    // Keep previous notifications while fetching.
    placeholderData: (previousData) => previousData,
  });

  // 2. Synchronize visual top alert badge count inside useUIStore in real-time
  const unreadCount = notifications.filter((alert) => !alert.is_read).length;

  useEffect(() => {
    setNotificationBadgeCount(unreadCount);
  }, [unreadCount, setNotificationBadgeCount]);

  // 3. Register individual read state modifier mutation
  const { mutate: triggerMarkSingleRead } = useMutation({
    mutationFn: ({ id, isRead }: { id: string; isRead: boolean }) =>
      notificationApi.updateNotificationRead(id, isRead),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: NOTIFICATIONS_QUERY_KEY,
      });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        toast.error(err.message || "Failed to update notification state.");
      }
    },
  });

  // 4. Register bulk one-click "Mark all as read" execution mutation
  const { mutate: triggerMarkAllRead, isPending: isMarkingAll } = useMutation({
    mutationFn: notificationApi.markAllNotificationsRead,
    onSuccess: async (response) => {
      toast.success(`Marked ${response.updated_count} alerts as read.`);
      await queryClient.invalidateQueries({
        queryKey: NOTIFICATIONS_QUERY_KEY,
      });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        toast.error(err.message || "Failed to process bulk read updates.");
      }
    },
  });

  // 5. Register single alert card deletion mutation
  const { mutate: triggerDeleteSingle } = useMutation({
    mutationFn: notificationApi.deleteNotification,
    onSuccess: async () => {
      toast.success("Notification removed.");
      await queryClient.invalidateQueries({
        queryKey: NOTIFICATIONS_QUERY_KEY,
      });
      const remaining = notifications.length - 1;

      if (remaining <= 0) {
        onClose();
      }
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        toast.error(err.message || "Failed to remove notification card.");
      }
    },
  });

  // Escape key handler to close the popover safely
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [isOpen, onClose]);

  const handleCardClick = useCallback(
    (id: string, isRead: boolean, workItemId: string | null): void => {
      // If unread, mark as read immediately out-of-band
      if (!isRead) {
        triggerMarkSingleRead({ id, isRead: true });
      }

      // Close popover dialog
      onClose();

      // If associated with a WorkItem, redirect the client to its details sheets
      if (workItemId) {
        navigate(ROUTES.WORK_ITEM_DETAILS.replace(":id", workItemId));
      }
    },
    [triggerMarkSingleRead, navigate, onClose]
  );

  if (!isOpen) {
    return null;
  }

  return (
    <>
      <div
        className={`absolute right-0 mt-3 w-80 sm:w-96 bg-card border border-border/80 rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[480px] z-50 animate-scale-in ${className}`}
      >
        {/* Top Header Controls Panel */}
        <header className="p-4 border-b border-border/40 flex items-center justify-between bg-muted/5 select-none">
          <div className="flex items-center space-x-2">
            <Bell className="h-4 w-4 text-primary" />
            <span className="text-xs font-extrabold uppercase tracking-wider">
              Alert Center
            </span>
          </div>

          <div className="flex items-center space-x-2">
            {notifications.some((n) => !n.is_read) && (
              <button
                type="button"
                onClick={() => triggerMarkAllRead()}
                disabled={isMarkingAll}
                className="inline-flex items-center text-[10px] font-black uppercase text-primary hover:bg-primary/10 px-2 py-1 rounded transition-all focus:outline-none"
                title="Mark all notifications as read"
              >
                {isMarkingAll ? (
                  <Loader2 className="h-3 w-3 animate-spin mr-1" />
                ) : (
                  <CheckCheck className="h-3 w-3 mr-1" />
                )}
                Mark All Read
              </button>
            )}

            <button
              type="button"
              onClick={onClose}
              className="p-1 rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-all"
              aria-label="Close notification tray"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto divide-y divide-border/20 scrollbar bg-background/50">
          {isLoading ? (
            <div className="p-4 space-y-3 animate-pulse">
              <div className="space-y-2">
                <div className="h-3 bg-muted/40 rounded w-2/3" />
                <div className="h-2.5 bg-muted/30 rounded w-full" />
              </div>
              <div className="space-y-2 pt-2">
                <div className="h-3 bg-muted/40 rounded w-1/2" />
                <div className="h-2.5 bg-muted/30 rounded w-full" />
              </div>
            </div>
          ) : error ? (
            <div className="p-8 text-center select-none text-muted-foreground">
              <AlertCircle className="h-7 w-7 mx-auto mb-2 text-destructive opacity-80" />
              <p className="text-xs font-bold">Failed to load alerts cache.</p>
            </div>
          ) : notifications.length === 0 ? (
            <div className="p-8 text-center select-none text-muted-foreground">
              <Clock className="h-8 w-8 mx-auto mb-2 opacity-35 animate-pulse" />
              <p className="text-xs font-bold leading-relaxed">
                Your alert workspace is clean.
              </p>
            </div>
          ) : (
            notifications.map((alert) => (
              <div
                key={alert.id}
                onClick={() =>
                  handleCardClick(alert.id, alert.is_read, alert.work_item_id)
                }
                className={`p-4 flex items-start space-x-3.5 transition-all duration-150 cursor-pointer hover:bg-muted/30 group relative ${
                  !alert.is_read &&
                  "bg-primary/5 dark:bg-primary/10/20 border-l-2 border-primary"
                }`}
              >
                <div className="mt-0.5 select-none">
                  <FileText
                    className={`h-4.5 w-4.5 ${
                      !alert.is_read ? "text-primary" : "text-muted-foreground"
                    }`}
                  />
                </div>

                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex justify-between items-baseline pr-4">
                    <h4
                      className={`text-xs truncate ${
                        !alert.is_read
                          ? "font-extrabold text-foreground"
                          : "font-semibold text-muted-foreground"
                      }`}
                    >
                      {alert.title}
                    </h4>
                  </div>

                  <p className="text-[11px] font-medium leading-relaxed leading-snug text-muted-foreground/90 pr-4 select-text">
                    {alert.message}
                  </p>

                  <span className="text-[9px] font-black text-muted-foreground/60 select-none block pt-1">
                    {formatDateTime(alert.created_at)}
                  </span>
                </div>

                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setNotificationToDelete(alert);
                  }}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 p-1.5 rounded bg-muted/20 hover:bg-destructive/10 text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                  title="Remove alert card"
                  aria-label={`Remove notification: ${alert.title}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      <ConfirmDialog
        open={notificationToDelete !== null}
        title="Delete Notification"
        message={
          notificationToDelete
            ? `Delete "${notificationToDelete.title}"? This action cannot be undone.`
            : ""
        }
        confirmText="Delete"
        cancelText="Cancel"
        onCancel={() => setNotificationToDelete(null)}
        onConfirm={() => {
          if (!notificationToDelete) return;

          triggerDeleteSingle(notificationToDelete.id);
          setNotificationToDelete(null);
        }}
      />
    </>
  );
};

export default NotificationTray;
