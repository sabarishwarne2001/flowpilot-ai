import React, { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bell, CheckCheck, Loader2 } from "lucide-react";

import { notificationApi } from "@/services/api/notification";
import { orgNotificationKeys } from "@/services/api/queryKeys";
import { organizationNotificationsPath } from "@/routes/tenantPaths";
import { formatDateTime } from "@/utils/formatters";

const PAGE_SIZE = 10;

export const OrganizationNotificationBell: React.FC<{
  readonly organizationId: string;
  readonly orgSlug: string;
}> = ({ organizationId, orgSlug }) => {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const feed = useQuery({
    queryKey: orgNotificationKeys.list(organizationId),
    queryFn: () =>
      notificationApi.getOrganizationNotifications(organizationId, {
        limit: PAGE_SIZE,
      }),
    enabled: Boolean(organizationId),
    refetchOnWindowFocus: true,
  });

  const items = useMemo(() => feed.data?.items ?? [], [feed.data]);
  const unread = feed.data?.unread_count ?? 0;

  const markRead = useMutation({
    mutationFn: (notificationId: string) =>
      notificationApi.updateOrganizationNotificationRead(
        organizationId,
        notificationId,
        true,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: orgNotificationKeys.all(organizationId),
      });
    },
  });

  const close = useCallback(() => setOpen(false), []);
  const badge = unread > 99 ? "99+" : String(unread);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            close();
          }
        }}
        aria-label="Open organization notifications"
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Organization notifications"
        className={`relative flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground ${
          open ? "bg-accent text-foreground" : ""
        }`}
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-destructive-foreground ring-2 ring-background"
            aria-label={`${unread} unread organization alerts`}
          >
            {badge}
          </span>
        )}
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            aria-hidden="true"
            onClick={close}
          />
          <div
            role="dialog"
            aria-label="Organization notifications"
            className="fp-popover absolute right-0 z-50 mt-2.5 w-80 overflow-hidden sm:w-96"
          >
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <p className="text-sm font-semibold">Organization alerts</p>
              {feed.isFetching ? (
                <Loader2
                  className="h-3.5 w-3.5 animate-spin text-muted-foreground"
                  aria-hidden
                />
              ) : null}
            </div>

            <div className="max-h-80 overflow-y-auto overscroll-contain">
              {feed.isLoading ? (
                <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                  Loading…
                </p>
              ) : feed.isError ? (
                <p
                  role="alert"
                  className="px-4 py-6 text-center text-sm text-destructive"
                >
                  Organization alerts could not be loaded.
                </p>
              ) : items.length === 0 ? (
                <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                  No organization alerts.
                </p>
              ) : (
                <ul className="divide-y divide-border/60">
                  {items.map((item) => (
                    <li
                      key={item.id}
                      className={`px-4 py-3 ${item.is_read ? "opacity-60" : ""}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium leading-snug">
                            {item.title}
                          </p>
                          {item.message ? (
                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {item.message}
                            </p>
                          ) : null}
                          <p className="mt-1 text-[11px] text-muted-foreground">
                            {formatDateTime(item.created_at)}
                          </p>
                        </div>
                        {!item.is_read && (
                          <button
                            type="button"
                            onClick={() => markRead.mutate(item.id)}
                            disabled={markRead.isPending}
                            title="Mark as read"
                            aria-label="Mark as read"
                            className="shrink-0 rounded-md border border-border p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                          >
                            <CheckCheck className="h-3.5 w-3.5" aria-hidden />
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="border-t border-border/60 px-4 py-2">
              <button
                type="button"
                onClick={() => {
                  close();
                  navigate(organizationNotificationsPath(orgSlug));
                }}
                className="text-xs font-semibold text-primary hover:underline"
              >
                View all organization notifications
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default OrganizationNotificationBell;
