import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell, Info, Loader2 } from "lucide-react";

import { getOrganizationNotifications } from "@/services/api/notification";
import { orgNotificationKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import type { Notification } from "@/types/notification";

const PAGE_SIZE = 25;

export const OrganizationNotifications: React.FC = () => {
  const { organization, organizationId } = useResolvedOrganization();
  const [showUnreadOnly, setShowUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: orgNotificationKeys.list(
      organizationId,
      showUnreadOnly ? false : undefined,
    ),
    queryFn: () =>
      getOrganizationNotifications(organizationId, {
        ...(showUnreadOnly ? { isRead: false } : {}),
        limit: PAGE_SIZE,
        offset,
      }),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
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

        <p className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          Organization notifications are read-only activity records.
        </p>

        {items.length === 0 ? (
          <div className="rounded-lg border border-border bg-card p-6 text-center">
            <Bell className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium text-foreground">
              {showUnreadOnly ? "Nothing unread" : "No notifications"}
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
                      {new Date(notification.created_at).toLocaleString()}
                    </p>
                  </div>
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
