/**
 * The workspace's first-run checklist (campaign session 4, §A.4).
 *
 * A new customer should reach a useful result within minutes: add a document (or try the built-in
 * sample invoice, an ordinary upload made by the server), see what Servabo read from it, ask the
 * assistant about it, and bring a colleague in. Each step ticks itself from the workspace's real
 * state, so the card is never out of step with what has been done; it disappears when all are done
 * or when the person hides it (remembered in this browser only).
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2, Circle, Loader2, Sparkles, X } from "lucide-react";

import { workItemApi } from "@/services/api/workItem";
import { getConversations } from "@/services/api/assistant";
import { listOrganizationMembers } from "@/services/api/organization";
import { assistantKeys, dashboardKeys, organizationKeys, workItemKeys } from "@/services/api/queryKeys";
import { assistantPath, organizationMembersPath, workItemDetailsPath } from "@/routes/tenantPaths";
import { ApiError } from "@/services/api/client";

interface GettingStartedProps {
  readonly workspaceId: string;
  readonly organizationId: string;
  readonly orgSlug: string;
  readonly workspaceSlug: string;
  readonly totalDocuments: number;
  readonly canUpload: boolean;
  readonly canInvite: boolean;
}

const storageKey = (workspaceId: string): string => `fp.getting-started.hidden.${workspaceId}`;

const isHidden = (workspaceId: string): boolean => {
  try {
    return window.localStorage.getItem(storageKey(workspaceId)) === "1";
  } catch {
    return false;
  }
};

export const GettingStarted: React.FC<GettingStartedProps> = ({
  workspaceId,
  organizationId,
  orgSlug,
  workspaceSlug,
  totalDocuments,
  canUpload,
  canInvite,
}) => {
  const queryClient = useQueryClient();
  const [hidden, setHidden] = useState(() => isHidden(workspaceId));

  const processed = useQuery({
    queryKey: workItemKeys.list(workspaceId, { page: 1, pageSize: 1, status: "COMPLETED" }),
    queryFn: () => workItemApi.getWorkItems(workspaceId, { page: 1, pageSize: 1, status: "COMPLETED" }),
    enabled: !hidden && totalDocuments > 0,
    refetchInterval: (query) => ((query.state.data?.items.length ?? 0) === 0 ? 5_000 : false),
  });
  const conversations = useQuery({
    queryKey: assistantKeys.conversations(workspaceId),
    queryFn: () => getConversations(workspaceId),
    enabled: !hidden,
  });
  const members = useQuery({
    queryKey: organizationKeys.members(organizationId, false),
    queryFn: () => listOrganizationMembers(organizationId),
    enabled: !hidden && canInvite,
  });

  const sample = useMutation({
    mutationFn: () => workItemApi.uploadSampleDocument(workspaceId),
    onSuccess: async () => {
      toast.success("Sample invoice added. Servabo is reading it now; it takes about a minute.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: dashboardKeys.all(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: workItemKeys.all(workspaceId) }),
      ]);
    },
    onError: (error: unknown) => {
      toast.error(error instanceof ApiError ? error.message : "The sample could not be added. Please try again.");
    },
  });

  const firstProcessed = processed.data?.items[0];
  const steps = [
    { key: "upload", done: totalDocuments > 0, title: "Add your first document" },
    { key: "read", done: Boolean(firstProcessed), title: "See what Servabo read from it" },
    { key: "ask", done: (conversations.data?.length ?? 0) > 0, title: "Ask the assistant about it" },
    ...(canInvite
      ? [{ key: "invite", done: (members.data?.seats_consumed ?? members.data?.total ?? 0) > 1, title: "Invite a colleague" }]
      : []),
  ];
  const doneCount = steps.filter((step) => step.done).length;

  if (hidden || doneCount === steps.length) {
    return null;
  }

  const hide = (): void => {
    try {
      window.localStorage.setItem(storageKey(workspaceId), "1");
    } catch {
      /* private window: hidden for this visit only */
    }
    setHidden(true);
  };

  const action = (key: string): React.ReactNode => {
    if (key === "upload" && canUpload) {
      return (
        <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          Drop a file above, or
          <button
            type="button"
            onClick={() => sample.mutate()}
            disabled={sample.isPending}
            className="fp-btn fp-btn-secondary h-7 text-xs"
          >
            {sample.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            Try a sample invoice
          </button>
        </span>
      );
    }
    if (key === "read" && firstProcessed) {
      return (
        <Link to={workItemDetailsPath(orgSlug, workspaceSlug, firstProcessed.id)} className="text-xs font-medium text-primary hover:underline">
          Open {firstProcessed.original_filename}
        </Link>
      );
    }
    if (key === "read" && totalDocuments > 0) {
      return <span className="text-xs text-muted-foreground">Reading your document now…</span>;
    }
    if (key === "ask") {
      return (
        <Link to={assistantPath(orgSlug, workspaceSlug)} className="text-xs font-medium text-primary hover:underline">
          Open the assistant
        </Link>
      );
    }
    if (key === "invite") {
      return (
        <Link to={organizationMembersPath(orgSlug)} className="text-xs font-medium text-primary hover:underline">
          Go to members
        </Link>
      );
    }
    return null;
  };

  return (
    <section aria-labelledby="getting-started" className="fp-card p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="getting-started" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
            <Sparkles className="h-4 w-4 text-primary" aria-hidden />
            Get started with Servabo
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {doneCount} of {steps.length} done. Each step ticks itself when you have done it.
          </p>
        </div>
        <button type="button" onClick={hide} aria-label="Hide getting started" className="rounded-md p-1 text-muted-foreground hover:bg-muted">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <ol className="mt-4 space-y-3" aria-label="Getting started steps">
        {steps.map((step) => (
          <li key={step.key} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {step.done ? (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden />
            ) : (
              <Circle className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            )}
            <span className={`text-sm ${step.done ? "text-muted-foreground line-through" : "text-foreground"}`}>
              {step.title}
              <span className="sr-only">{step.done ? " (done)" : " (to do)"}</span>
            </span>
            {/* A read document stays one click away: the step is done when it is read, not seen. */}
            {step.done && step.key !== "read" ? null : action(step.key)}
          </li>
        ))}
      </ol>
    </section>
  );
};

export default GettingStarted;
