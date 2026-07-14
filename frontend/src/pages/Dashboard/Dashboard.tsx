import React, { useCallback } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { toast } from "sonner";

import {
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  RefreshCw,
  TrendingUp,
} from "lucide-react";

import { dashboardApi } from "@/services/api/dashboard";
import { workItemApi } from "@/services/api/workItem";

import { UploadTray } from "@/components/common/UploadTray";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";

import { SkeletonCard } from "@/components/common/skeletons/SkeletonCard";
import { SkeletonTable } from "@/components/common/skeletons/SkeletonTable";

import { ApiError } from "@/services/api/client";
import { formatDateTime } from "@/utils/formatters";

/* ============================================================================
   Query Keys
============================================================================ */

const DASHBOARD_QUERY_KEY = ["dashboard-overview"] as const;

/* ============================================================================
   Activity Badge Styling
============================================================================ */

const EVENT_BADGES = {
  PROCESS_COMPLETED: "bg-emerald-500/10 text-emerald-500",

  UPLOAD_COMPLETED: "bg-primary/10 text-primary",

  AUTOMATION_TRIGGERED: "bg-indigo-500/10 text-indigo-500",

  PROCESS_STARTED: "bg-amber-500/10 text-amber-500",

  PROCESS_FAILED: "bg-destructive/10 text-destructive",
} as const;

/* ============================================================================
   Dashboard
============================================================================ */

export const Dashboard: React.FC = () => {
  const queryClient = useQueryClient();

  const {
    data: metrics,
    isLoading,
    error,
  } = useQuery({
    queryKey: DASHBOARD_QUERY_KEY,

    queryFn: dashboardApi.getDashboardOverview,

    staleTime: 5_000,

    refetchOnWindowFocus: true,

    placeholderData: (previousData) => previousData,

    refetchInterval: (query) => {
      const data = query.state.data;

      if (!data) {
        return false;
      }

      if (data.processing_status.total > 0) {
        return 2_000;
      }

      return false;
    },
  });
  /* ==========================================================================
       Reprocess Mutation
    ========================================================================== */

  const { mutate: triggerReprocess, isPending: isReprocessing } = useMutation({
    mutationFn: workItemApi.reprocessWorkItem,

    onSuccess: async () => {
      toast.success("Document scheduled for reprocessing.");

      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: DASHBOARD_QUERY_KEY,
        }),

        queryClient.invalidateQueries({
          queryKey: ["automation-logs"],
        }),

        queryClient.invalidateQueries({
          queryKey: ["automation-rules"],
        }),

        queryClient.invalidateQueries({
          queryKey: ["notifications"],
        }),
      ]);
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message ?? "Unable to reprocess the document.");

        return;
      }

      toast.error("Unexpected network error.");
    },
  });

  /* ==========================================================================
       Upload Success
    ========================================================================== */

  const handleUploadSuccess = useCallback(async (): Promise<void> => {
    console.log("UPLOAD SUCCESS CALLBACK");
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: DASHBOARD_QUERY_KEY,
      }),

      queryClient.invalidateQueries({
        queryKey: ["work-items"],
      }),
    ]);
  }, [queryClient]);

  /* ==========================================================================
       Loading State
    ========================================================================== */

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>

        <SkeletonTable rows={5} />
      </div>
    );
  }

  /* ==========================================================================
       Error State
    ========================================================================== */

  if (error || !metrics) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <ErrorState
          title="Unable to load dashboard"
          description="An unexpected error occurred while retrieving your dashboard analytics. Please try again."
          onRetry={async () => {
            await queryClient.invalidateQueries({
              queryKey: DASHBOARD_QUERY_KEY,
            });
          }}
        />
      </div>
    );
  }

  const metricCards = [
    {
      title: "Total Documents",
      value: metrics.total_work_items,
      icon: FileText,
    },

    {
      title: "Processed Today",
      value: metrics.processed_today,
      icon: CheckCircle2,
    },

    {
      title: "Processing",
      value: metrics.processing_status.total,
      icon: Loader2,
    },

    {
      title: "Success Rate",
      value: `${metrics.automation_success_rate}%`,
      icon: TrendingUp,
    },
  ];

  return (
    <div className="space-y-8">
      {/* ======================================================
            Upload Section
        ====================================================== */}

      <UploadTray onUploadSuccess={handleUploadSuccess} />

      {/* ======================================================
            KPI Cards
        ====================================================== */}

      <section
        aria-label="Dashboard Metrics"
        className="grid gap-6 md:grid-cols-2 xl:grid-cols-4"
      >
        {metricCards.map((card) => (
          <div
            key={card.title}
            className="rounded-xl border border-border/60 bg-card p-6 shadow-sm transition-colors dark:border-border/40"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-semibold text-muted-foreground">
                  {card.title}
                </p>

                <p className="mt-2 text-3xl font-extrabold tracking-tight">
                  {card.value}
                </p>
              </div>

              <div className="rounded-xl bg-primary/10 p-3 text-primary">
                <card.icon
                  className={`h-6 w-6 ${
                    card.title === "Processing" ? "animate-spin" : ""
                  }`}
                />
              </div>
            </div>
          </div>
        ))}
      </section>

      {/* ======================================================
            Recent Activity
        ====================================================== */}

      <section className="rounded-xl border border-border/60 bg-card p-6 shadow-sm dark:border-border/40">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold">Recent Activity</h2>

            <p className="mt-1 text-sm text-muted-foreground">
              Latest document processing events.
            </p>
          </div>
        </div>

        <div className="space-y-3">
          {metrics.recent_activity.length === 0 ? (
            <EmptyState
              icon={Clock}
              title="No recent activity"
              description="No document processing activity has been recorded yet. Upload a document to begin."
            />
          ) : (
            metrics.recent_activity.map((activity) => (
              <div
                key={activity.id}
                className="flex items-center justify-between rounded-lg border border-border/40 bg-muted/10 p-4 transition-colors hover:bg-muted/20"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs font-bold ${
                        EVENT_BADGES[activity.event_type]
                      }`}
                    >
                      {activity.event_type.replaceAll("_", " ")}
                    </span>

                    <span className="text-sm font-semibold">
                      {activity.description}
                    </span>
                  </div>

                  <p className="mt-2 text-xs text-muted-foreground">
                    {formatDateTime(activity.timestamp)}
                  </p>
                </div>

                {activity.event_type === "PROCESS_FAILED" &&
                  activity.work_item_id && (
                    <button
                      type="button"
                      disabled={isReprocessing}
                      onClick={() => {
                        if (activity.work_item_id) {
                          triggerReprocess(activity.work_item_id);
                        }
                      }}
                      className="ml-4 flex items-center rounded-lg border border-border bg-background px-3 py-2 text-xs font-semibold transition-colors hover:bg-muted/40 disabled:pointer-events-none disabled:opacity-50"
                    >
                      <RefreshCw className="mr-2 h-3.5 w-3.5" />
                      Retry
                    </button>
                  )}
              </div>
            ))
          )}
        </div>
      </section>

      {/* ======================================================
            Document Distribution
        ====================================================== */}

      <section className="rounded-xl border border-border/60 bg-card p-6 shadow-sm dark:border-border/40">
        <div className="mb-6">
          <h2 className="text-lg font-bold">Document Distribution</h2>

          <p className="mt-1 text-sm text-muted-foreground">
            Breakdown of processed document types.
          </p>
        </div>
        {metrics.document_type_distribution.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="No processed documents"
            description="Upload and process documents to view document distribution analytics."
          />
        ) : (
          <div className="space-y-5">
            {metrics.document_type_distribution.map((item) => (
              <div key={item.document_type}>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-semibold">
                    {item.document_type}
                  </span>

                  <span className="text-sm font-bold">
                    {item.count} ({item.percentage}
                    %)
                  </span>
                </div>

                <div className="h-2 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all duration-500"
                    style={{
                      width: `${item.percentage}%`,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};

Dashboard.displayName = "Dashboard";

export default Dashboard;
