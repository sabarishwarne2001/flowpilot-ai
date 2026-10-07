import { formatTimestamp } from "@/utils/displayTime";
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Loader2, Mail, MailOpen } from "lucide-react";
import { toast } from "sonner";

import {
  getOrganizationNotifications,
  updateOrganizationNotificationRead,
} from "@/services/api/notification";
import { orgNotificationKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  NOTIFICATION_CATEGORIES,
  NOTIFICATION_CATEGORY_LABELS,
  type Notification,
  type NotificationCategory,
} from "@/types/notification";

const PAGE_SIZE = 25;

export const OrganizationNotifications: React.FC = () => {
  const { organization, organizationId } = useResolvedOrganization();
  const queryClient = useQueryClient();
  const [showUnreadOnly, setShowUnreadOnly] = useState(false);
  const [category, setCategory] = useState<"ALL" | NotificationCategory>("ALL");
  const [offset, setOffset] = useState(0);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    // The offset and category are in the key: without them "Next" showed the cached first page.
    queryKey: orgNotificationKeys.page(
      organizationId,
      showUnreadOnly ? false : undefined,
      category,
      offset,
    ),
    queryFn: () =>
      getOrganizationNotifications(organizationId, {
        ...(showUnreadOnly ? { isRead: false } : {}),
        ...(category !== "ALL" ? { category } : {}),
        limit: PAGE_SIZE,
        offset,
      }),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const toggleRead = useMutation({
    mutationFn: ({ id, isRead }: { id: string; isRead: boolean }) =>
      updateOrganizationNotificationRead(organizationId, id, isRead),
    onSuccess: async (_data, { isRead }) => {
      await queryClient.invalidateQueries({ queryKey: orgNotificationKeys.all(organizationId) });
      toast.success(isRead ? "Marked as read." : "Marked as unread.");
    },
    onError: () => toast.error("The notification couldn't be updated."),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading notifications…
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <p role="alert" className="text-sm text-destructive">
          Notifications couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-6">
        <header>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
            <Bell className="h-5 w-5" />
            Organization notifications
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {organization.organization_name} · Events that concern the whole organization.
          </p>
        </header>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
          <span className="text-sm text-foreground">
            <strong>{total}</strong> total
            {data && data.unread_count > 0 && (
              <span className="text-muted-foreground">
                {" "}
                · {data.unread_count} unread
              </span>
            )}
          </span>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-foreground">
              <span className="text-muted-foreground">Category</span>
              <select
                aria-label="Category"
                value={category}
                onChange={(event) => {
                  setCategory(event.target.value as "ALL" | NotificationCategory);
                  setOffset(0);
                }}
                className="rounded-md border border-border bg-background px-2 py-1 text-sm"
              >
                <option value="ALL">All categories</option>
                {NOTIFICATION_CATEGORIES.map((value) => (
                  <option key={value} value={value}>
                    {NOTIFICATION_CATEGORY_LABELS[value]}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={showUnreadOnly}
                onChange={(event) => {
                  setShowUnreadOnly(event.target.checked);
                  setOffset(0);
                }}
              />
              Unread only
            </label>
          </div>
        </div>

        {items.length === 0 ? (
          <div className="rounded-lg border border-border bg-card p-6 text-center">
            <Bell className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium text-foreground">
              {showUnreadOnly || category !== "ALL" ? "Nothing matches these filters" : "No notifications"}
            </p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Organization-level events will appear here.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border bg-card">
            {items.map((notification: Notification) => (
              <li
                key={notification.id}
                className={`p-4 ${notification.is_read ? "opacity-70" : ""}`}
              >
                <div className="flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                      {notification.title}
                      {!notification.is_read && (
                        <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                      )}
                    </p>
                    <p className="mt-0.5 text-sm text-muted-foreground">
                      {notification.message}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatTimestamp(notification.created_at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      toggleRead.mutate({ id: notification.id, isRead: !notification.is_read })
                    }
                    disabled={toggleRead.isPending}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-60"
                  >
                    {notification.is_read ? (
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
                </div>
              </li>
            ))}
          </ul>
        )}

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0 || isFetching}
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-50"
            >
              Previous
            </button>
            <span className="text-xs text-muted-foreground">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </span>
            <button
              type="button"
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total || isFetching}
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-50"
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default OrganizationNotifications;
