import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bell, FileText } from "lucide-react";
import { notificationApi } from "@/services/api/notification";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { SkeletonTable } from "@/components/common/skeletons/SkeletonTable";
import { formatDateTime } from "@/utils/formatters";
import { ROUTES } from "@/constants/routes";

// Centralized Query Cache Keys matching our standard specifications
const NOTIFICATIONS_QUERY_KEY = ["notifications"] as const;

/**
 * High-fidelity, user-isolated Notifications list panel for FlowPilot AI.
 *
 * Leverages the pre-existing notificationApi and structures in-app alerts
 * chronologically, offering inline routing redirects to source documents.
 */
export const Notifications: React.FC = () => {
  // 1. Query raw alerts data from PostgreSQL using the centralized Query context
  const {
    data: notifications = [],
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: NOTIFICATIONS_QUERY_KEY,
    queryFn: () => notificationApi.getNotifications(),
    staleTime: 10_000, // 10-second cache stale boundaries
    placeholderData: (previousData) => previousData, // Prevents cumulative visual shifts
  });

  // Render Full-Page loading skeletons on initial startup
  if (isLoading && notifications.length === 0) {
    return (
      <div className="space-y-6">
        <header className="space-y-1">
          <h1 className="text-2xl font-extrabold tracking-tight">
            Notifications
          </h1>
          <div className="h-4 bg-muted/40 rounded w-64 animate-pulse" />
        </header>

        <SkeletonTable rows={5} />
      </div>
    );
  }

  // Composes our universal ErrorState on connection failures
  if (error) {
    return (
      <div className="min-h-[70vh] flex items-center justify-center p-6 bg-background">
        <ErrorState
          title="Failed to sync alerts"
          description="The workspace was unable to establish connection parameters to fetch notifications history."
          onRetry={() => {
            void refetch();
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* --- Section 1: Page Title Headers --- */}
      <header className="flex justify-between items-center select-none">
        <div className="space-y-1">
          <h1 className="text-2xl font-extrabold tracking-tight">
            Notifications
          </h1>
          <p className="text-sm text-muted-foreground font-semibold leading-relaxed">
            View and manage your workspace notifications.
          </p>
        </div>
      </header>

      {/* --- Section 2: Notifications List Viewport --- */}
      <div className="space-y-4">
        {notifications.length === 0 ? (
          /* Composes our universal EmptyState element if the database reports 0 rows */
          <div className="p-8">
            <EmptyState
              icon={Bell}
              title="No notifications"
              description="You don't have any notifications yet."
            />
          </div>
        ) : (
          <div className="space-y-3">
            {notifications.map((alert) => (
              <article
                key={alert.id}
                className={`p-5 bg-card border rounded-xl shadow-sm transition-all duration-200 flex flex-col md:flex-row md:items-center justify-between gap-4
                  ${
                    alert.is_read
                      ? "border-border/40 opacity-75"
                      : "border-border/80 border-l-4 border-l-primary"
                  }`}
                role="article"
                aria-label={`Notification: ${alert.title}`}
              >
                {/* Information Node columns */}
                <div className="flex items-start space-x-4 min-w-0">
                  <div
                    className={`p-2.5 rounded-lg flex-shrink-0 mt-0.5 select-none
                    ${
                      alert.is_read
                        ? "bg-muted text-muted-foreground"
                        : "bg-primary/10 text-primary"
                    }`}
                  >
                    <Bell className="h-4.5 w-4.5" />
                  </div>

                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center space-x-2 w-full flex-wrap gap-1.5">
                      <h2
                        className={`text-sm leading-none ${
                          alert.is_read
                            ? "font-bold text-foreground/80"
                            : "font-black text-foreground"
                        }`}
                      >
                        {alert.title}
                      </h2>
                      {/* State status badge */}
                      {!alert.is_read && (
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-black tracking-wide bg-primary text-primary-foreground leading-none uppercase">
                          New
                        </span>
                      )}
                    </div>

                    <p className="text-xs font-semibold leading-relaxed text-muted-foreground pr-4 select-text">
                      {alert.message}
                    </p>

                    <span className="text-[10px] font-black text-muted-foreground/60 select-none block pt-0.5">
                      {formatDateTime(alert.created_at)}
                    </span>
                  </div>
                </div>

                {/* Operations quick actions links (Redirects to linked WorkItemDetails sheet) */}
                {alert.work_item_id && (
                  <div className="flex-shrink-0 self-end md:self-center">
                    <Link
                      to={ROUTES.WORK_ITEM_DETAILS.replace(
                        ":id",
                        alert.work_item_id
                      )}
                      className="inline-flex items-center px-3 py-1.5 border border-border bg-background hover:bg-muted text-muted-foreground hover:text-foreground text-[11px] font-black tracking-wider uppercase rounded-lg transition-all focus:outline-none"
                      title="Inspect Associated Document Analytics"
                    >
                      <FileText className="h-3.5 w-3.5 mr-1.5 flex-shrink-0" />
                      <span>Inspect Document</span>
                    </Link>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default Notifications;
