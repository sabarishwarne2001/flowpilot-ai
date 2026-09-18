import React, { useState } from "react";
import { Download, RotateCcw, Tag, Trash2, X } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { runBulkAction, type BulkActionResult } from "@/services/api/ingestion";
import { ingestionKeys, workItemKeys } from "@/services/api/queryKeys";

/**
 * ARCH-38 — the bulk action bar.
 *
 * Delete asks for confirmation with a count, because "Delete" next to a
 * checkbox that might hold 200 rows is the most destructive control in the
 * product. Refusals come back per item, so a delete blocked by a retention
 * hold names the documents rather than failing the whole request.
 */

export interface BulkActionBarProps {
  readonly workspaceId: string;
  readonly selectedIds: readonly string[];
  readonly onClear: () => void;
  readonly onDone?: (result: BulkActionResult) => void;
}

const newIdempotencyKey = (): string =>
  globalThis.crypto?.randomUUID?.() ??
  `bulk-${Date.now()}-${Math.random().toString(16).slice(2)}`;

export const BulkActionBar: React.FC<BulkActionBarProps> = ({
  workspaceId,
  selectedIds,
  onClear,
  onDone,
}) => {
  const queryClient = useQueryClient();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const [showTagInput, setShowTagInput] = useState(false);
  const [refusals, setRefusals] = useState<BulkActionResult["results"]>([]);

  const mutation = useMutation({
    mutationFn: (input: {
      action: "delete" | "reprocess" | "export" | "tag";
      tags?: readonly string[];
      export_format?: "csv" | "json";
    }) =>
      runBulkAction(workspaceId, {
        action: input.action,
        ids: selectedIds,
        idempotency_key: newIdempotencyKey(),
        ...(input.tags ? { tags: input.tags } : {}),
        ...(input.export_format ? { export_format: input.export_format } : {}),
      }),
    onSuccess: (result) => {
      setRefusals(result.results.filter((row) => row.outcome === "refused"));
      if (result.export_body) {
        const blob = new Blob([result.export_body], {
          type: result.export_mime_type ?? "text/csv",
        });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download =
          result.export_mime_type === "application/json"
            ? "documents.json"
            : "documents.csv";
        anchor.click();
        URL.revokeObjectURL(url);
      }
      void queryClient.invalidateQueries({
        queryKey: workItemKeys.all(workspaceId),
      });
      void queryClient.invalidateQueries({
        queryKey: ingestionKeys.tags(workspaceId),
      });
      setConfirmingDelete(false);
      setShowTagInput(false);
      setTagDraft("");
      onDone?.(result);
    },
  });

  if (selectedIds.length === 0) {
    return null;
  }

  const count = selectedIds.length;
  const noun = count === 1 ? "document" : "documents";

  return (
    <div className="sticky bottom-0 z-10 rounded-lg border border-border bg-background p-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">
          {count} {noun} selected
        </span>
        <div className="flex-1" />

        <button
          type="button"
          onClick={() => mutation.mutate({ action: "reprocess" })}
          disabled={mutation.isPending}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Reprocess
        </button>

        <button
          type="button"
          onClick={() => setShowTagInput((value) => !value)}
          disabled={mutation.isPending}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
        >
          <Tag className="h-3.5 w-3.5" aria-hidden="true" />
          Tag
        </button>

        <button
          type="button"
          onClick={() =>
            mutation.mutate({ action: "export", export_format: "csv" })
          }
          disabled={mutation.isPending}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
        >
          <Download className="h-3.5 w-3.5" aria-hidden="true" />
          Export
        </button>

        <button
          type="button"
          onClick={() => setConfirmingDelete(true)}
          disabled={mutation.isPending}
          className="inline-flex items-center gap-1.5 rounded-md border border-destructive/40 px-2.5 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/5 disabled:opacity-50"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          Delete
        </button>

        <button
          type="button"
          aria-label="Clear selection"
          onClick={onClear}
          className="rounded p-1 text-muted-foreground hover:bg-muted"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {showTagInput && (
        <div className="mt-2 flex items-center gap-2">
          <input
            value={tagDraft}
            onChange={(event) => setTagDraft(event.target.value)}
            placeholder="q3-review"
            aria-label="Tag to apply"
            className="flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
          />
          <button
            type="button"
            disabled={!tagDraft.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                action: "tag",
                tags: tagDraft
                  .split(",")
                  .map((value) => value.trim())
                  .filter(Boolean),
              })
            }
            className="rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
          >
            Apply
          </button>
        </div>
      )}

      {confirmingDelete && (
        <div
          role="alertdialog"
          aria-label="Confirm deletion"
          className="mt-2 rounded-md border border-destructive/40 bg-destructive/5 p-3"
        >
          <p className="text-xs">
            Delete {count} {noun}? This cannot be undone. Documents under a
            retention hold will be kept and listed below.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => mutation.mutate({ action: "delete" })}
              disabled={mutation.isPending}
              className="rounded-md bg-destructive px-2.5 py-1.5 text-xs font-medium text-destructive-foreground disabled:opacity-50"
            >
              Delete {count} {noun}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              className="rounded-md border border-border px-2.5 py-1.5 text-xs font-medium"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {refusals.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs">
          {refusals.slice(0, 10).map((row) => (
            <li key={row.work_item_id} className="text-muted-foreground">
              <span className="font-medium">{row.code}</span>
              {row.detail ? ` — ${row.detail}` : ""}
            </li>
          ))}
          {refusals.length > 10 && (
            <li className="text-muted-foreground">
              and {refusals.length - 10} more
            </li>
          )}
        </ul>
      )}
    </div>
  );
};

export default BulkActionBar;
