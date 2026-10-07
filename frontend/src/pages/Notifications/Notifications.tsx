import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  Bell,
  CheckCheck,
  FileText,
  Mail,
  MailOpen,
  Settings2,
  ShieldAlert,
  Trash2,
  Workflow,
} from "lucide-react";
import { notificationApi } from "@/services/api/notification";
import { ApiError } from "@/services/api/client";
import { useTenant } from "@/hooks/useTenant";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { SkeletonTable } from "@/components/common/skeletons/SkeletonTable";
import { formatDateTime } from "@/utils/formatters";
import { workItemDetailsPath } from "@/routes/tenantPaths";
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";
import { notificationKeys, keepPreviousWithinWorkspace } from "@/services/api/queryKeys";
import {
  NOTIFICATION_CATEGORIES,
  NOTIFICATION_CATEGORY_LABELS,
  type Notification,
  type NotificationCategory,
} from "@/types/notification";

type ReadFilter = "all" | "unread";
type CategoryFilter = "ALL" | NotificationCategory;

const CATEGORY_ICONS: Record<NotificationCategory, React.ComponentType<{ className?: string }>> = {
  DOCUMENT: FileText,
  AUTOMATION: Workflow,
  EMAIL: Mail,
  SYSTEM: Settings2,
  SECURITY: ShieldAlert,
};

const isCategory = (value: string | undefined): value is NotificationCategory =>
  value !== undefined && (NOTIFICATION_CATEGORIES as readonly string[]).includes(value);

const errorMessage = (error: unknown, fallback: string): string =>
  error instanceof ApiError && error.message ? error.message : fallback;

/**
 * The workspace notification inbox.
 *
 * N-020 item 2: filter by read state (All / Unread) and by category, mark one
 * notification read or unread again, mark everything read, and delete. Each
 * filter combination is its own query, and every change invalidates the whole
 * notification scope so the header tray and badge agree with this page.
 */
export const Notifications: React.FC = () => {
  const { state: tenantState } = useTenant();
  const workspaceId = useActiveWorkspaceId();
  const queryClient = useQueryClient();
  const [readFilter, setReadFilter] = useState<ReadFilter>("all");
  const [category, setCategory] = useState<CategoryFilter>("ALL");

  const {
    data: notifications = [],
    isLoading,
    error,
    refetch,
  } = useQuery<readonly Notification[], Error>({
    queryKey: notificationKeys.inbox(workspaceId!, readFilter, category),
    queryFn: () =>
      notificationApi.getNotifications(
        workspaceId!,
        readFilter === "unread" ? false : undefined,
        category === "ALL" ? undefined : category,
      ),
    enabled: Boolean(workspaceId),
    staleTime: 10_000,
    placeholderData: keepPreviousWithinWorkspace<readonly Notification[]>(workspaceId!),
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: notificationKeys.all(workspaceId!) });

  const toggleRead = useMutation({
    mutationFn: ({ id, isRead }: { id: string; isRead: boolean }) =>
      notificationApi.updateNotificationRead(workspaceId!, id, isRead),
    onSuccess: async (_data, { isRead }) => {
      await refresh();
      toast.success(isRead ? "Marked as read." : "Marked as unread.");
    },
    onError: (err) => toast.error(errorMessage(err, "The notification couldn't be updated.")),
  });

  const markAll = useMutation({
    mutationFn: () => notificationApi.markAllNotificationsRead(workspaceId!),
    onSuccess: async (result) => {
      await refresh();
      toast.success(
        result.updated_count === 1
          ? "1 notification marked as read."
          : `${result.updated_count} notifications marked as read.`,
      );
    },
    onError: (err) => toast.error(errorMessage(err, "Notifications couldn't be marked as read.")),
  });

  const remove = useMutation({
    mutationFn: (id: string) => notificationApi.deleteNotification(workspaceId!, id),
    onSuccess: async () => {
      await refresh();
      toast.success("Notification deleted.");
    },
    onError: (err) => toast.error(errorMessage(err, "The notification couldn't be deleted.")),
  });

  const unreadShown = notifications.filter((alert) => !alert.is_read).length;

  if (isLoading && notifications.length === 0) {
    return (
      <div className="space-y-6">
        <header className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Notifications</h1>
          <div className="h-4 bg-muted/40 rounded w-64 animate-pulse" />
        </header>
        <SkeletonTable rows={5} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-[70vh] flex items-center justify-center p-6 bg-background">
        <ErrorState
          title="Notifications couldn't be loaded"
          description="Check your connection and try again."
          onRetry={() => {
            void refetch();
          }}
        />
      </div>
    );
  }

  const tabClass = (active: boolean) =>
    `rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
      active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"
    }`;

  const filtered = readFilter !== "all" || category !== "ALL";

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3 select-none">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Notifications</h1>
          <p className="text-sm text-muted-foreground font-semibold leading-relaxed">
            View and manage your workspace notifications.
          </p>
        </div>
        {unreadShown > 0 && (
          <button
            type="button"
            onClick={() => markAll.mutate()}
            disabled={markAll.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-2 text-xs font-semibold hover:bg-muted disabled:opacity-60"
          >
            <CheckCheck className="h-4 w-4" aria-hidden />
            Mark all read
          </button>
        )}
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Read state" className="inline-flex gap-1 rounded-lg border border-border bg-card p-1">
          {(["all", "unread"] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={readFilter === value}
              onClick={() => setReadFilter(value)}
              className={tabClass(readFilter === value)}
            >
              {value === "all" ? "All" : "Unread"}
            </button>
          ))}
        </div>
        <div role="tablist" aria-label="Category" className="inline-flex flex-wrap gap-1 rounded-lg border border-border bg-card p-1">
          <button
            type="button"
            role="tab"
            aria-selected={category === "ALL"}
            onClick={() => setCategory("ALL")}
            className={tabClass(category === "ALL")}
          >
            All categories
          </button>
          {NOTIFICATION_CATEGORIES.map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={category === value}
              onClick={() => setCategory(value)}
              className={tabClass(category === value)}
            >
              {NOTIFICATION_CATEGORY_LABELS[value]}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-4" role="tabpanel" aria-label="Notifications">
        {notifications.length === 0 ? (
          <div className="p-8">
            <EmptyState
              icon={Bell}
              title={filtered ? "Nothing matches these filters" : "No notifications"}
              description={
                filtered
                  ? "Try another category, or show read notifications too."
                  : "You don't have any notifications yet."
              }
            />
          </div>
        ) : (
          <div className="space-y-3">
            {notifications.map((alert: Notification) => {
              const Icon = isCategory(alert.notification_type) ? CATEGORY_ICONS[alert.notification_type] : Bell;
              const label = isCategory(alert.notification_type)
                ? NOTIFICATION_CATEGORY_LABELS[alert.notification_type]
                : null;
              return (
                <article
                  key={alert.id}
                  className={`p-5 bg-card border rounded-xl shadow-sm transition-all duration-200 flex flex-col md:flex-row md:items-center justify-between gap-4 ${
                    alert.is_read ? "border-border/40" : "border-border/80 border-l-4 border-l-primary"
                  }`}
                  aria-label={`Notification: ${alert.title}`}
                >
                  <div className="flex items-start space-x-4 min-w-0">
                    <div
                      className={`p-2.5 rounded-lg flex-shrink-0 mt-0.5 select-none ${
                        alert.is_read ? "bg-muted text-muted-foreground" : "bg-primary/10 text-primary"
                      }`}
                    >
                      <Icon className="h-4 w-4" />
                    </div>

                    <div className="min-w-0 space-y-1">
                      <div className="flex items-center w-full flex-wrap gap-1.5">
                        <h2
                          className={`text-sm leading-none ${
                            alert.is_read ? "font-semibold text-foreground/80" : "font-semibold text-foreground"
                          }`}
                        >
                          {alert.title}
                        </h2>
                        {!alert.is_read && (
                          <span className="fp-btn-primary inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold tracking-wide bg-primary text-primary-foreground leading-none uppercase">
                            New
                          </span>
                        )}
                        {label && (
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-border text-[10px] font-semibold text-muted-foreground leading-none">
                            {label}
                          </span>
                        )}
                      </div>

                      <p className="text-xs font-medium leading-relaxed text-muted-foreground pr-4 select-text">
                        {alert.message}
                      </p>

                      <span className="text-[10px] font-semibold text-muted-foreground/70 select-none block pt-0.5">
                        {formatDateTime(alert.created_at)}
                      </span>
                    </div>
                  </div>

                  <div className="flex flex-shrink-0 flex-wrap items-center gap-2 self-end md:self-center">
                    {alert.work_item_id && tenantState.status === "ready" && (
                      <Link
                        to={workItemDetailsPath(
                          tenantState.organization.organization_slug,
                          tenantState.workspace.slug,
                          alert.work_item_id,
                        )}
                        className="inline-flex items-center px-3 py-1.5 border border-border bg-background hover:bg-muted text-muted-foreground hover:text-foreground text-[11px] font-semibold tracking-wide uppercase rounded-lg transition-all"
                        title="Open the document this notification is about"
                      >
                        <FileText className="h-3.5 w-3.5 mr-1.5 flex-shrink-0" />
                        <span>Inspect Document</span>
                      </Link>
                    )}
                    <button
                      type="button"
                      onClick={() => toggleRead.mutate({ id: alert.id, isRead: !alert.is_read })}
                      disabled={toggleRead.isPending}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-[11px] font-semibold text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-60"
                    >
                      {alert.is_read ? (
                        <>
                          <Mail className="h-3.5 w-3.5" aria-hidden />
                          Mark as unread
                        </>
                      ) : (
                        <>
                          <MailOpen className="h-3.5 w-3.5" aria-hidden />
                          Mark as read
                        </>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => remove.mutate(alert.id)}
                      disabled={remove.isPending}
                      aria-label={`Delete notification: ${alert.title}`}
                      title="Delete"
                      className="inline-flex items-center rounded-lg border border-border bg-background p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-60"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default Notifications;
