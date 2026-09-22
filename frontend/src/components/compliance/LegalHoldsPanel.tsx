/**
 * HARDENING-T2:D9 — legal holds, finally visible.
 *
 * The ARCH-38 routes to list, place and release retention holds existed with
 * no screen: a compliance officer could not see or place a hold. A hold stops
 * retention sweeps and erasure from deleting the data it covers. Listing is
 * open to workspace viewers; placing and releasing need a workspace admin
 * (the server enforces this). Every change is audited server-side.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Lock, Unlock } from "lucide-react";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";
import { listRetentionHolds, type RetentionHold } from "@/services/api/ingestion";
import { placeRetentionHold, releaseRetentionHold } from "@/services/api/retentionHolds";
import { formatTimestamp } from "@/utils/displayTime";

interface LegalHoldsPanelProps {
  readonly workspaceId: string;
  readonly canManage: boolean;
}

const holdsKey = (workspaceId: string) => ["retention-holds", workspaceId] as const;

export const LegalHoldsPanel: React.FC<LegalHoldsPanelProps> = ({ workspaceId, canManage }) => {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [reference, setReference] = useState("");
  const [workItemId, setWorkItemId] = useState("");

  const holds = useQuery({
    queryKey: holdsKey(workspaceId),
    queryFn: () => listRetentionHolds(workspaceId),
    enabled: Boolean(workspaceId),
  });

  const place = useMutation({
    mutationFn: () =>
      placeRetentionHold(workspaceId, {
        reason: reason.trim(),
        reference: reference.trim() || null,
        work_item_id: workItemId.trim() || null,
      }),
    onSuccess: () => {
      toast.success("Legal hold placed.");
      setReason("");
      setReference("");
      setWorkItemId("");
      void queryClient.invalidateQueries({ queryKey: holdsKey(workspaceId) });
    },
    onError: (error) => toast.error(errorMessage(error, "The hold could not be placed.")),
  });

  const release = useMutation({
    mutationFn: (hold: RetentionHold) => releaseRetentionHold(workspaceId, hold.id),
    onSuccess: () => {
      toast.success("Legal hold released.");
      void queryClient.invalidateQueries({ queryKey: holdsKey(workspaceId) });
    },
    onError: (error) => toast.error(errorMessage(error, "The hold could not be released.")),
  });

  const active = (holds.data ?? []).filter((hold) => !hold.released_at);
  const released = (holds.data ?? []).filter((hold) => hold.released_at);
  const reasonValid = reason.trim().length >= 3;

  return (
    <section className="rounded-xl border border-border bg-card p-6" aria-labelledby="legal-holds-heading">
      <header className="mb-4">
        <h2 id="legal-holds-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
          <Lock className="h-4 w-4" aria-hidden="true" />
          Legal holds
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          A hold stops retention sweeps and erasure requests from deleting what it covers: the whole workspace,
          or a single document.
        </p>
      </header>

      {holds.isError ? (
        <ErrorState
          title="Legal holds could not be loaded"
          description={errorMessage(holds.error, "The server did not return the holds for this workspace.")}
          onRetry={() => void holds.refetch()}
        />
      ) : holds.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading holds…
        </div>
      ) : active.length === 0 ? (
        <EmptyState icon={Lock} title="No active holds" description="Nothing in this workspace is currently preserved beyond its retention policy." />
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {active.map((hold) => (
            <li key={hold.id} className="flex items-start justify-between gap-4 p-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium text-foreground">{hold.reason}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {hold.work_item_id ? "One document" : "Whole workspace"}
                  {hold.reference ? ` · Ref ${hold.reference}` : ""} · placed {formatTimestamp(hold.placed_at)}
                </p>
              </div>
              {canManage && (
                <button
                  type="button"
                  onClick={() => release.mutate(hold)}
                  disabled={release.isPending}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-semibold hover:bg-muted disabled:opacity-50"
                >
                  <Unlock className="h-3.5 w-3.5" aria-hidden="true" /> Release
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canManage && (
        <form
          className="mt-5 grid gap-3 sm:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (reasonValid) {
              place.mutate();
            }
          }}
        >
          <div className="sm:col-span-3">
            <label htmlFor="hold-reason" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Reason (recorded in the audit log)
            </label>
            <input
              id="hold-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={255}
              placeholder="e.g. Litigation hold — Acme v. FlowPilot"
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
            />
          </div>
          <div>
            <label htmlFor="hold-reference" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Reference (optional)
            </label>
            <input
              id="hold-reference"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              maxLength={128}
              placeholder="Case or matter number"
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
            />
          </div>
          <div>
            <label htmlFor="hold-document" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Document ID (optional)
            </label>
            <input
              id="hold-document"
              value={workItemId}
              onChange={(event) => setWorkItemId(event.target.value)}
              placeholder="Blank = whole workspace"
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 font-mono text-sm focus:border-primary focus:outline-none"
            />
          </div>
          <div className="flex items-end">
            <button
              type="submit"
              disabled={!reasonValid || place.isPending}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {place.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              Place hold
            </button>
          </div>
        </form>
      )}

      {released.length > 0 && (
        <details className="mt-4 text-xs text-muted-foreground">
          <summary className="cursor-pointer font-semibold">Released holds ({released.length})</summary>
          <ul className="mt-2 space-y-1">
            {released.map((hold) => (
              <li key={hold.id}>
                {hold.reason} · released {formatTimestamp(hold.released_at ?? null)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
};

export default LegalHoldsPanel;
