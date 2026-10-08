/**
 * Phase 2 — one TruthMesh conflict: what disagrees, in which documents, what it puts at risk, and
 * the decision. Each document's value sits side by side (the child against the parent it draws on,
 * the earlier invoice against the later one), with the sentence it came from when there is one.
 * Resolving or dismissing asks for a few words, which the audit export carries.
 */
import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, CircleSlash, Eye, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { Modal } from "@/components/batches/shared";
import { KindChip, money, SeverityBadge } from "@/components/truthmesh/shared";
import { errorMessage } from "@/services/api/errors";
import { decideConflict, meshKeys } from "@/services/api/truthmesh";
import type { ConflictStatus, MeshConflict } from "@/types/truthmesh";
import { formatTimestamp } from "@/utils/displayTime";

interface ConflictCardProps {
  readonly workspaceId: string;
  readonly conflict: MeshConflict;
  readonly canDecide: boolean;
  readonly onOpenDocument?: (workItemId: string) => void;
  readonly defaultOpen?: boolean;
}

const STATUS_LABEL: Readonly<Record<ConflictStatus, string>> = {
  OPEN: "Open",
  ACKNOWLEDGED: "Acknowledged",
  RESOLVED: "Resolved",
  DISMISSED: "Dismissed",
};

export const ConflictCard: React.FC<ConflictCardProps> = ({ workspaceId, conflict, canDecide, onOpenDocument, defaultOpen = false }) => {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(defaultOpen);
  const [asking, setAsking] = useState<ConflictStatus | null>(null);
  const [note, setNote] = useState("");

  const decide = useMutation({
    mutationFn: (body: { status: ConflictStatus; note?: string }) => decideConflict(workspaceId, conflict.id, body),
    onSuccess: async (updated) => {
      toast.success(`Conflict ${STATUS_LABEL[updated.status].toLowerCase()}`);
      setAsking(null);
      setNote("");
      await queryClient.invalidateQueries({ queryKey: meshKeys.all(workspaceId) });
    },
    onError: (error) => toast.error(errorMessage(error, "The decision was not saved.")),
  });

  const active = conflict.status === "OPEN" || conflict.status === "ACKNOWLEDGED";

  return (
    <article
      className={`fp-card overflow-hidden ${active ? "" : "opacity-75"}`}
      data-testid="mesh-conflict"
      data-kind={conflict.kind}
      data-severity={conflict.severity}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-muted/40"
      >
        <span className="mt-0.5 text-muted-foreground">
          {open ? <ChevronDown className="h-4 w-4" aria-hidden /> : <ChevronRight className="h-4 w-4" aria-hidden />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <SeverityBadge severity={conflict.severity} />
            <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] font-medium text-foreground/80">{conflict.kind_label}</span>
            {conflict.status !== "OPEN" ? (
              <span className="rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {STATUS_LABEL[conflict.status]}
                {conflict.auto_resolved ? " automatically" : ""}
              </span>
            ) : null}
          </div>
          <h3 className="mt-1.5 text-[14px] font-semibold leading-snug text-foreground [overflow-wrap:anywhere]">{conflict.title}</h3>
        </div>
        {conflict.exposure_micros ? (
          <div className="shrink-0 text-right">
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">At risk</div>
            <div className="text-sm font-semibold tabular-nums text-red-600 dark:text-red-400">
              {money(conflict.exposure_micros, conflict.currency)}
            </div>
          </div>
        ) : null}
      </button>

      {open ? (
        <div className="space-y-3 border-t border-border/60 px-4 py-3">
          <p className="text-[13px] leading-relaxed text-foreground/85">{conflict.summary}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {conflict.document_values.map((value) => (
              <div key={`${value.work_item_id}-${value.role ?? ""}`} className="min-w-0 rounded-lg border border-border/70 bg-muted/25 p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenDocument?.(value.work_item_id)}
                    className="min-w-0 truncate text-left text-xs font-semibold text-primary hover:underline"
                  >
                    {value.title}
                  </button>
                  {value.role ? <span className="shrink-0 text-[10.5px] uppercase tracking-wide text-muted-foreground">{value.role}</span> : null}
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <KindChip kind={value.kind} label={value.kind.replace(/_/g, " ").toLowerCase()} />
                  <span className="min-w-0 truncate font-mono text-[12.5px] font-semibold text-foreground">{value.display}</span>
                </div>
                {value.quote ? (
                  <blockquote className="mt-1.5 border-l-2 border-primary/40 pl-2 text-[11.5px] italic leading-snug text-muted-foreground [overflow-wrap:anywhere]">
                    {value.quote}
                  </blockquote>
                ) : null}
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span>
              First seen {formatTimestamp(conflict.first_seen_at)}
              {conflict.resolution_note ? ` · “${conflict.resolution_note}”` : ""}
            </span>
            {canDecide ? (
              <div className="flex flex-wrap gap-1.5">
                {active ? (
                  <>
                    {conflict.status === "OPEN" ? (
                      <button
                        type="button"
                        className="fp-btn fp-btn-ghost inline-flex items-center gap-1 px-2 py-1 text-xs"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ status: "ACKNOWLEDGED" })}
                      >
                        <Eye className="h-3.5 w-3.5" aria-hidden />
                        Acknowledge
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="fp-btn fp-btn-secondary inline-flex items-center gap-1 px-2 py-1 text-xs"
                      onClick={() => setAsking("DISMISSED")}
                    >
                      <CircleSlash className="h-3.5 w-3.5" aria-hidden />
                      Not a conflict
                    </button>
                    <button
                      type="button"
                      className="fp-btn fp-btn-primary inline-flex items-center gap-1 px-2 py-1 text-xs"
                      onClick={() => setAsking("RESOLVED")}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                      Resolve
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="fp-btn fp-btn-ghost inline-flex items-center gap-1 px-2 py-1 text-xs"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ status: "OPEN" })}
                  >
                    <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                    Reopen
                  </button>
                )}
              </div>
            ) : (
              <span>Contributors decide conflicts.</span>
            )}
          </div>
        </div>
      ) : null}

      {asking ? (
        <Modal
          title={asking === "RESOLVED" ? "Resolve this conflict" : "Mark as not a conflict"}
          description={
            asking === "RESOLVED"
              ? "Say how it was resolved: the audit export carries your words with the conflict."
              : "Say why this is not a conflict. It stays dismissed when the mesh is rebuilt."
          }
          onClose={() => setAsking(null)}
          busy={decide.isPending}
          footer={
            <>
              <button type="button" className="fp-btn fp-btn-secondary" onClick={() => setAsking(null)} disabled={decide.isPending}>
                Cancel
              </button>
              <button
                type="button"
                className="fp-btn fp-btn-primary inline-flex items-center gap-1.5"
                disabled={note.trim().length < 3 || decide.isPending}
                onClick={() => decide.mutate({ status: asking, note: note.trim() })}
              >
                {decide.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                {asking === "RESOLVED" ? "Resolve" : "Dismiss"}
              </button>
            </>
          }
        >
          <label className="block text-sm font-medium" htmlFor={`note-${conflict.id}`}>
            Reason
          </label>
          <textarea
            id={`note-${conflict.id}`}
            className="fp-input mt-1.5 min-h-[5rem] w-full resize-y"
            value={note}
            autoFocus
            onChange={(event) => setNote(event.target.value)}
            placeholder={asking === "RESOLVED" ? "e.g. INV-1002 cancelled with the supplier; credit note requested" : "e.g. the scan is the paper copy of the same invoice"}
          />
        </Modal>
      ) : null}
    </article>
  );
};

export default ConflictCard;
