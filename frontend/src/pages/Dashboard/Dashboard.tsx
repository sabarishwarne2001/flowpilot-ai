import React, { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  RefreshCw,
  Shapes,
  TrendingUp,
  UploadCloud,
  Zap,
} from "lucide-react";

import { dashboardApi } from "@/services/api/dashboard";
import { workItemApi } from "@/services/api/workItem";
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";
import { dashboardKeys, invalidateWorkspace, keepPreviousWithinWorkspace } from "@/services/api/queryKeys";

import { UploadTray } from "@/components/common/UploadTray";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { SkeletonCard } from "@/components/common/skeletons/SkeletonCard";
import { SkeletonTable } from "@/components/common/skeletons/SkeletonTable";

import { ApiError } from "@/services/api/client";
import { useOptionalTenant } from "@/routes/TenantContext";
import { canCreateContent } from "@/permissions/workspacePermissions";
import { workItemDetailsPath, workItemsPath } from "@/routes/tenantPaths";
import { formatDateTime } from "@/utils/formatters";
import type { DashboardDocTypeDistribution, DashboardEventType } from "@/types/dashboard";

// Each event wears a colour, an icon and its name, never the colour alone.
const EVENTS: Readonly<Record<DashboardEventType, { readonly tone: string; readonly icon: React.ElementType; readonly label: string }>> = {
  PROCESS_COMPLETED: { tone: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400", icon: CheckCircle2, label: "Process completed" },
  UPLOAD_COMPLETED: { tone: "bg-primary/10 text-primary dark:text-[hsl(213_94%_72%)]", icon: UploadCloud, label: "Upload completed" },
  AUTOMATION_TRIGGERED: { tone: "bg-indigo-500/10 text-indigo-700 dark:text-indigo-300", icon: Zap, label: "Automation triggered" },
  PROCESS_STARTED: { tone: "bg-amber-500/10 text-amber-700 dark:text-amber-400", icon: Clock, label: "Process started" },
  PROCESS_FAILED: { tone: "bg-destructive/10 text-destructive", icon: AlertTriangle, label: "Process failed" },
};

const KINDS_SHOWN = 8;

/** "IMAGE/PNG" reads as "PNG"; "PDF" stays "PDF". */
const formatLabel = (value: string): string => (value.includes("/") ? value.split("/").pop() ?? value : value).toUpperCase();

interface KpiCardProps {
  readonly title: string;
  readonly value: React.ReactNode;
  readonly hint: React.ReactNode;
  readonly icon: React.ElementType;
  readonly testId: string;
  readonly danger?: boolean;
  readonly spin?: boolean;
  readonly to?: string | undefined;
  readonly className?: string;
}

const KpiCard: React.FC<KpiCardProps> = ({ title, value, hint, icon: Icon, testId, danger = false, spin = false, to, className: extra = "" }) => {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="fp-eyebrow">{title}</p>
        <span
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border shadow-inner-highlight ${
            danger ? "border-destructive/30 bg-destructive/10 text-destructive" : "border-border bg-muted/60 text-muted-foreground"
          }`}
        >
          <Icon className={`h-4 w-4 ${spin ? "animate-spin" : ""}`} aria-hidden />
        </span>
      </div>
      <p className={`fp-num mt-2 text-[28px] font-semibold leading-none tracking-tight ${danger ? "text-destructive" : "text-foreground"}`}>
        {value}
      </p>
      <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
        {hint}
        {to ? <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" aria-hidden /> : null}
      </p>
    </>
  );
  const className = `fp-card group relative flex min-h-[7.5rem] flex-col justify-between overflow-hidden p-4 ${extra}`;
  return to ? (
    <Link to={to} className={`${className} transition-colors hover:border-primary/40`} data-testid={testId}>
      {body}
    </Link>
  ) : (
    <div className={className} data-testid={testId}>
      {body}
    </div>
  );
};

/** One bar per kind, longest first; the name and count are written beside every bar. */
const KindBars: React.FC<{ readonly items: readonly DashboardDocTypeDistribution[] }> = ({ items }) => {
  const shown = items.slice(0, KINDS_SHOWN);
  const rest = items.slice(KINDS_SHOWN);
  const widest = Math.max(...shown.map((item) => item.count), 1);
  return (
    <div>
      <ul className="space-y-3" aria-label="Documents by type">
        {shown.map((item) => (
          <li key={item.document_type} title={`${item.document_type}: ${item.count} documents (${item.percentage}%)`}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
              <span className="truncate font-medium text-foreground">{item.document_type}</span>
              <span className="fp-num shrink-0 text-muted-foreground">
                {item.count} <span className="text-[11px]">· {item.percentage}%</span>
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
              <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${(item.count / widest) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
      {rest.length > 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          and {rest.length} more type{rest.length === 1 ? "" : "s"} ({rest.reduce((sum, item) => sum + item.count, 0)} documents)
        </p>
      ) : null}
    </div>
  );
};

export const Dashboard: React.FC = () => {
  const queryClient = useQueryClient();
  const workspaceId = useActiveWorkspaceId();
  // Viewers cannot upload or reprocess (the server refuses them); neither is offered to them.
  const resolvedTenant = useOptionalTenant();
  const canUpload = resolvedTenant ? canCreateContent(resolvedTenant.workspaceRole) : false;
  const orgSlug = resolvedTenant?.organization.organization_slug ?? "";
  const workspaceSlug = resolvedTenant?.workspace.slug ?? "";
  const documentsPath = resolvedTenant ? workItemsPath(orgSlug, workspaceSlug) : undefined;

  const {
    data: metrics,
    isLoading,
    error,
  } = useQuery({
    queryKey: dashboardKeys.overview(workspaceId!),
    queryFn: () => dashboardApi.getDashboardOverview(workspaceId!),
    enabled: Boolean(workspaceId),
    staleTime: 5_000,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousWithinWorkspace(workspaceId!),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) {return false;}
      if (data.processing_status.total > 0) {
        return 2_000;
      }
      return false;
    },
  });

  const { mutate: triggerReprocess, isPending: isReprocessing } = useMutation({
    mutationFn: (workItemId: string) => workItemApi.reprocessWorkItem(workspaceId!, workItemId),
    onSuccess: async () => {
      toast.success("Document scheduled for reprocessing.");
      await invalidateWorkspace(queryClient, workspaceId!);
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message ?? "Unable to reprocess the document.");
        return;
      }
      toast.error("Unexpected network error.");
    },
  });

  const handleUploadSuccess = useCallback(async (): Promise<void> => {
    await invalidateWorkspace(queryClient, workspaceId!);
  }, [queryClient, workspaceId]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="h-12" />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {Array.from({ length: 5 }, (_, index) => <SkeletonCard key={index} />)}
        </div>
        <SkeletonTable rows={5} />
      </div>
    );
  }

  if (error || !metrics) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <ErrorState
          title="Unable to load dashboard"
          description="An unexpected error occurred while retrieving your dashboard analytics. Please try again."
          onRetry={async () => {
            if (workspaceId) {
              await queryClient.invalidateQueries({
                queryKey: dashboardKeys.overview(workspaceId),
              });
            }
          }}
        />
      </div>
    );
  }

  const inFlight = metrics.processing_status.total;
  const failed = metrics.failed_count;
  const kinds = metrics.classification_distribution ?? [];
  const classified = kinds.reduce((sum, item) => sum + item.count, 0);
  const rate = metrics.automation_success_rate;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
          <p className="mt-0.5 truncate text-[13px] text-muted-foreground">
            {resolvedTenant?.workspace.workspace_name ?? "This workspace"}: documents, processing and recent activity.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {inFlight > 0 ? (
            <span role="status" className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground">
              <span className="relative flex h-2 w-2" aria-hidden>
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
              </span>
              Updating live
            </span>
          ) : null}
          {documentsPath ? (
            <Link to={documentsPath} className="fp-btn fp-btn-secondary h-8 text-xs">
              All documents <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : null}
        </div>
      </header>

      {canUpload && <UploadTray compact onUploadSuccess={handleUploadSuccess} />}

      <section aria-label="Dashboard Metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <KpiCard
          title="Total Documents"
          value={metrics.total_work_items}
          hint={metrics.total_work_items === 0 ? "Upload a document to begin" : `${classified} classified by type`}
          icon={FileText}
          testId="kpi-total"
          to={documentsPath}
        />
        <KpiCard
          title="Processed Today"
          value={metrics.processed_today}
          hint="Finished processing today"
          icon={CheckCircle2}
          testId="kpi-today"
        />
        <KpiCard
          title="Processing"
          value={inFlight}
          hint={inFlight > 0 ? `${metrics.processing_status.queued} queued · ${metrics.processing_status.processing} running` : "Nothing waiting"}
          icon={Loader2}
          spin={inFlight > 0}
          testId="kpi-processing"
        />
        <KpiCard
          title="Failed"
          value={failed}
          hint={failed > 0 ? "Open the failed documents" : "No failed documents"}
          icon={AlertTriangle}
          danger={failed > 0}
          testId="kpi-failed"
          to={failed > 0 && documentsPath ? `${documentsPath}?status=FAILED` : undefined}
        />
        <KpiCard
          title="Success Rate"
          // F-161: nothing finished yet is not "100%".
          value={rate === null ? "—" : `${rate}%`}
          hint={rate === null ? "Nothing has finished yet" : "Of finished documents"}
          icon={TrendingUp}
          testId="kpi-success"
          // Five cards in a two-column grid: the last one takes the whole row on a phone.
          className="col-span-2 md:col-span-1"
        />
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="fp-card min-w-0 p-5" aria-labelledby="recent-activity">
          <div className="mb-4">
            <h2 id="recent-activity" className="text-[15px] font-semibold tracking-tight">Recent Activity</h2>
            <p className="mt-0.5 text-[13px] text-muted-foreground">Latest document processing events.</p>
          </div>

          {metrics.recent_activity.length === 0 ? (
            <EmptyState
              icon={Clock}
              title="No recent activity"
              description="No document processing activity has been recorded yet. Upload a document to begin."
            />
          ) : (
            <ol className="relative">
              {metrics.recent_activity.map((activity) => {
                const event = EVENTS[activity.event_type] ?? EVENTS.PROCESS_STARTED;
                const EventIcon = event.icon;
                return (
                  <li
                    key={activity.id}
                    className="group relative flex items-center justify-between gap-4 rounded-lg py-2 pl-7 pr-2 transition-colors before:absolute before:bottom-0 before:left-[9px] before:top-0 before:w-px before:bg-border first:before:top-1/2 last:before:bottom-1/2 hover:bg-muted/40"
                  >
                    <span
                      aria-hidden="true"
                      className="absolute left-[5px] top-1/2 h-[9px] w-[9px] -translate-y-1/2 rounded-full border-2 border-card bg-muted-foreground/50 ring-1 ring-border group-hover:bg-primary"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide ${event.tone}`}>
                          <EventIcon className="h-3 w-3" aria-hidden />
                          {event.label}
                        </span>
                        {activity.work_item_id && resolvedTenant ? (
                          <Link
                            to={workItemDetailsPath(orgSlug, workspaceSlug, activity.work_item_id)}
                            className="min-w-0 truncate text-sm font-medium text-foreground hover:text-primary hover:underline"
                          >
                            {activity.description}
                          </Link>
                        ) : (
                          <span className="min-w-0 truncate text-sm font-medium text-foreground">{activity.description}</span>
                        )}
                      </div>
                      <p className="fp-num mt-0.5 text-xs text-muted-foreground">{formatDateTime(activity.timestamp)}</p>
                    </div>

                    {canUpload && activity.event_type === "PROCESS_FAILED" && activity.work_item_id ? (
                      <button
                        type="button"
                        disabled={isReprocessing}
                        onClick={() => {
                          if (activity.work_item_id) {
                            triggerReprocess(activity.work_item_id);
                          }
                        }}
                        className="fp-btn fp-btn-secondary shrink-0 text-xs"
                      >
                        <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                        Retry
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
        </section>

        <section className="fp-card self-start p-5" aria-labelledby="document-types">
          <div className="mb-4">
            <h2 id="document-types" className="text-[15px] font-semibold tracking-tight">Document types</h2>
            <p className="mt-0.5 text-[13px] text-muted-foreground">What the classifier found in this workspace.</p>
          </div>
          {kinds.length === 0 ? (
            <EmptyState
              icon={Shapes}
              title="No classified documents"
              description="Each processed document is classified (invoice, purchase order, contract, ...). The mix appears here."
            />
          ) : (
            <KindBars items={kinds} />
          )}

          {metrics.document_type_distribution.length > 0 ? (
            <div className="mt-5 border-t border-border pt-4">
              <p className="fp-eyebrow mb-2">File formats</p>
              <ul className="flex flex-wrap gap-1.5" aria-label="Documents by file format">
                {metrics.document_type_distribution.map((item) => (
                  <li
                    key={item.document_type}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-0.5 text-xs"
                    title={`${item.percentage}% of documents`}
                  >
                    <span className="font-semibold">{formatLabel(item.document_type)}</span>
                    <span className="fp-num text-muted-foreground">{item.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
};

Dashboard.displayName = "Dashboard";
export default Dashboard;
