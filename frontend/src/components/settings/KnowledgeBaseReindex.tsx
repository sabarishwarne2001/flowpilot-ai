import React, { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { errorMessage } from "@/services/api/errors";
import { pollUnlessRefused } from "@/services/api/polling";
import { getReindexStatus, reindexKnowledgeBase } from "@/services/api/workItem";
import type { ReindexStatus } from "@/types/workItem";
import { formatTimestamp } from "@/utils/displayTime";

interface Props {
  readonly workspaceId: string;
  readonly canManage: boolean;
}

/** How often the card asks while a reindex runs; idle, it asks once on mount. */
const POLL_WHILE_RUNNING_MS = 2_500;

const reindexStatusKey = (workspaceId: string) =>
  ["knowledge", "reindex-status", workspaceId] as const;

/**
 * Settings -> "Knowledge Base Reindexing" (F-141).
 *
 * The request is accepted with 202 and the work happens on the worker, so the
 * card reads the run's progress from the status route: polled every few
 * seconds while jobs are waiting or running, not at all once they settle. The
 * button is disabled for as long as a run is active (and while the request is
 * in flight), so a second click cannot look like a first one, and a toast says
 * when the run ends and how it went.
 */
export const KnowledgeBaseReindex: React.FC<Props> = ({ workspaceId, canManage }) => {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const status = useQuery({
    queryKey: reindexStatusKey(workspaceId),
    queryFn: () => getReindexStatus(workspaceId),
    enabled: canManage && Boolean(workspaceId),
    refetchInterval: (query) =>
      query.state.data?.state === "running"
        ? pollUnlessRefused(POLL_WHILE_RUNNING_MS)(query)
        : false,
    refetchIntervalInBackground: false,
  });

  // A run this card watched go from running to idle ends with a toast. A run
  // that was already over when the page opened does not.
  const sawRunning = useRef(false);
  useEffect(() => {
    const data = status.data;
    if (!data) {return;}
    if (data.state === "running") {
      sawRunning.current = true;
      return;
    }
    if (sawRunning.current) {
      sawRunning.current = false;
      announceFinished(data);
    }
  }, [status.data]);

  const reindex = useMutation({
    mutationFn: () => reindexKnowledgeBase(workspaceId),
    onSuccess: (result) => {
      setConfirming(false);
      if (result.total_documents === 0) {
        toast.info("There are no completed documents in this workspace to reindex yet.");
      } else if (result.queued === 0) {
        toast.info("Every document is already waiting to be re-embedded. Nothing new was queued.");
      } else {
        // The run may settle before the next status read sees it running; the
        // completion toast must still follow.
        sawRunning.current = true;
        toast.success(
          `Re-embedding ${result.queued} document${result.queued === 1 ? "" : "s"} in the background.`,
        );
      }
      void queryClient.invalidateQueries({ queryKey: reindexStatusKey(workspaceId) });
    },
    onError: (error) => {
      setConfirming(false);
      toast.error(errorMessage(error, "Reindexing couldn't be started. Please try again."));
    },
  });

  if (!canManage) {return null;}

  const running = status.data?.state === "running";
  const busy = running || reindex.isPending;

  return (
    <div className="fp-card p-6">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-foreground">
            <Database className="h-5 w-5 text-primary" aria-hidden />
            Knowledge Base Reindexing
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Rebuilds vector search embeddings for every completed document in this workspace.
          </p>
        </div>
        {!confirming && (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            disabled={busy || status.isLoading}
            aria-busy={busy}
            className="fp-btn fp-btn-secondary"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-4 w-4" aria-hidden />
            )}
            {busy ? "Reindexing…" : "Reindex knowledge base…"}
          </button>
        )}
      </header>

      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Recommended after changing chunking parameters or embedding models. Existing documents
          stay searchable while the background worker processes the reindex queue.
        </p>

        <ReindexStatusCard
          data={status.data}
          loading={status.isLoading}
          failed={status.isError}
          onRetry={() => void status.refetch()}
        />

        {confirming && (
          <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-4">
            <p className="flex items-start gap-2 text-sm text-foreground">
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" aria-hidden />
              <span>
                Every document will be re-embedded. This queues background jobs on the worker fleet.
              </span>
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => reindex.mutate()}
                disabled={busy}
                aria-busy={reindex.isPending}
                className="fp-btn fp-btn-primary"
              >
                {reindex.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
                {reindex.isPending ? "Starting…" : "Start reindexing"}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={reindex.isPending}
                className="fp-btn fp-btn-secondary"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

function announceFinished(data: ReindexStatus): void {
  const run = data.latest;
  if (!run) {return;}
  if (run.failed > 0) {
    toast.warning(
      `Reindex finished: ${run.completed} of ${run.total} re-embedded, ${run.failed} could not be.`,
    );
  } else {
    toast.success(
      `Reindex complete: ${run.completed} document${run.completed === 1 ? "" : "s"} re-embedded.`,
    );
  }
}

interface StatusCardProps {
  readonly data: ReindexStatus | undefined;
  readonly loading: boolean;
  readonly failed: boolean;
  readonly onRetry: () => void;
}

const ReindexStatusCard: React.FC<StatusCardProps> = ({ data, loading, failed, onRetry }) => {
  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Checking the reindex status…
      </div>
    );
  }
  if (failed || !data) {
    return (
      <div
        role="alert"
        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/30 bg-destructive/[0.06] p-3 text-sm text-destructive"
      >
        The reindex status could not be loaded.
        <button type="button" onClick={onRetry} className="fp-btn fp-btn-ghost text-sm">
          Try again
        </button>
      </div>
    );
  }

  const run = data.latest;
  if (data.state === "running" && run) {
    const settled = run.completed + run.failed;
    const percent = run.total > 0 ? Math.round((settled / run.total) * 100) : 0;
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="reindex-status"
        data-state="running"
        className="space-y-2 rounded-lg border border-primary/25 bg-primary/[0.05] p-3"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="flex items-center gap-2 font-medium text-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />
            Re-embedding in progress…
          </span>
          <span className="tabular-nums text-muted-foreground">
            {settled} of {run.total} documents
          </span>
        </div>
        <div
          className="h-1.5 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-label="Reindex progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-500"
            style={{ width: `${Math.max(percent, 4)}%` }}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          Started {formatTimestamp(run.requested_at)}. Search keeps working while this runs; this
          card updates by itself.
        </p>
      </div>
    );
  }

  if (!run) {
    return (
      <div
        role="status"
        data-testid="reindex-status"
        data-state="never"
        className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground"
      >
        <Clock3 className="h-4 w-4" aria-hidden />
        This workspace has not been reindexed yet.
      </div>
    );
  }

  const hadFailures = run.failed > 0;
  return (
    <div
      role="status"
      data-testid="reindex-status"
      data-state="idle"
      className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${
        hadFailures
          ? "border-amber-500/30 bg-amber-500/[0.06]"
          : "border-emerald-500/25 bg-emerald-500/[0.06]"
      }`}
    >
      {hadFailures ? (
        <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" aria-hidden />
      ) : (
        <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-600" aria-hidden />
      )}
      <div className="min-w-0">
        <p className="font-medium text-foreground">
          Last completed on {formatTimestamp(data.last_completed_at ?? run.finished_at)}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {run.completed} of {run.total} document{run.total === 1 ? "" : "s"} re-embedded
          {hadFailures
            ? `; ${run.failed} could not be (retry them by reindexing again).`
            : "."}
        </p>
      </div>
    </div>
  );
};

export default KnowledgeBaseReindex;
