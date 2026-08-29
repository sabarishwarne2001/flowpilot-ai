import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Database, Loader2 } from "lucide-react";

import { reindexKnowledgeBase } from "@/services/api/workItem";
import type { ReindexResult } from "@/types/workItem";

interface Props {
  readonly workspaceId: string;
  readonly canManage: boolean;
}

export const KnowledgeBaseReindex: React.FC<Props> = ({
  workspaceId,
  canManage,
}) => {
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<ReindexResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reindex = useMutation({
    mutationFn: () => reindexKnowledgeBase(workspaceId),
    onSuccess: (data) => {
      setError(null);
      setConfirming(false);
      setResult(data);
    },
    onError: () => {
      setConfirming(false);
      setError("Reindexing couldn't be started. Please try again.");
    },
  });

  if (!canManage) return null;

  return (
    <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
      <header className="border-b border-border pb-3 mb-4">
        <h2 className="flex items-center gap-2 text-lg font-bold text-foreground">
          <Database className="h-5 w-5 text-primary" />
          Knowledge Base Reindexing
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Rebuilds vector search embeddings for every completed document in this workspace.
        </p>
      </header>

      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Recommended after changing chunking parameters or embedding models. Existing documents
          stay searchable while the background worker processes the reindex queue.
        </p>

        {error && (
          <p role="alert" className="text-sm text-destructive font-medium">
            {error}
          </p>
        )}

        {result && (
          <p
            role="status"
            className="rounded-lg border border-border bg-muted/30 p-3 text-sm text-foreground"
          >
            {result.detail}
            {result.queued === 0 && (
              <span className="mt-1 block text-xs text-muted-foreground">
                No completed documents found in this workspace to reindex.
              </span>
            )}
          </p>
        )}

        {confirming ? (
          <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-4">
            <p className="flex items-start gap-2 text-sm text-foreground">
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
              <span>
                Every document will be re-embedded. This queues background jobs on the worker fleet.
              </span>
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => reindex.mutate()}
                disabled={reindex.isPending}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {reindex.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Start reindexing
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={reindex.isPending}
                className="rounded-lg border border-border bg-background px-4 py-2 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-60"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => {
              setError(null);
              setResult(null);
              setConfirming(true);
            }}
            className="rounded-lg border border-border bg-background px-4 py-2 text-sm font-medium text-foreground hover:bg-muted"
          >
            Reindex knowledge base…
          </button>
        )}
      </div>
    </div>
  );
};

export default KnowledgeBaseReindex;
