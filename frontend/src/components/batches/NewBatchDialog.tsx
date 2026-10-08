/**
 * Phase 1 — create a batch, or add documents to an existing one.
 *
 * From the Batch operations page the person picks documents here (searchable, newest first).
 * From the Documents page the selection is already made and only the batch is chosen.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Layers, Loader2, Search } from "lucide-react";

import { addBatchDocuments, batchKeys, createBatch, listBatches } from "@/services/api/batches";
import { errorMessage } from "@/services/api/errors";
import { workItemApi } from "@/services/api/workItem";
import { Modal } from "@/components/batches/shared";

interface Props {
  readonly workspaceId: string;
  /** Documents chosen elsewhere (the Documents page). Absent: pick them here. */
  readonly presetIds?: readonly string[];
  /** Where a created batch opens. */
  readonly detailPath: (batchId: string) => string;
  readonly onClose: () => void;
  readonly onDone?: () => void;
}

export const NewBatchDialog: React.FC<Props> = ({ workspaceId, presetIds, detailPath, onClose, onDone }) => {
  const client = useQueryClient();
  const navigate = useNavigate();
  const fromSelection = presetIds !== undefined;
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [existing, setExisting] = useState("");
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<readonly string[]>([]);

  const documents = useQuery({
    queryKey: ["batches", workspaceId, "picker", search],
    queryFn: () => workItemApi.getWorkItems(workspaceId, { page: 1, pageSize: 100, ...(search.trim() ? { search: search.trim() } : {}), sortBy: "created_at", sortOrder: "desc" }),
    enabled: !fromSelection,
  });
  const batches = useQuery({
    queryKey: batchKeys.list(workspaceId, "ACTIVE"),
    queryFn: () => listBatches(workspaceId, "ACTIVE"),
    enabled: fromSelection,
  });

  const ids = fromSelection ? presetIds : picked;
  const rows = documents.data?.items ?? [];
  const allPicked = rows.length > 0 && rows.every((row) => picked.includes(row.id));

  const submit = useMutation({
    mutationFn: async () => {
      if (mode === "existing" && existing) {
        const updated = await addBatchDocuments(workspaceId, existing, ids);
        return { id: updated.id, created: false, name: updated.name };
      }
      const created = await createBatch(workspaceId, {
        name: name.trim(),
        description: description.trim(),
        work_item_ids: ids,
      });
      return { id: created.id, created: true, name: created.name };
    },
    onSuccess: async (result) => {
      await client.invalidateQueries({ queryKey: batchKeys.all(workspaceId) });
      toast.success(
        result.created
          ? `Batch "${result.name}" created with ${ids.length} document${ids.length === 1 ? "" : "s"}.`
          : `${ids.length} document${ids.length === 1 ? "" : "s"} added to "${result.name}".`,
      );
      onDone?.();
      onClose();
      navigate(detailPath(result.id));
    },
    onError: (error) => toast.error(errorMessage(error, "The batch could not be saved.")),
  });

  const canSubmit = ids.length > 0 && (mode === "existing" ? Boolean(existing) : Boolean(name.trim()));
  const existingOptions = useMemo(() => batches.data?.items ?? [], [batches.data]);

  return (
    <Modal
      title={fromSelection ? "Add to a batch" : "New batch"}
      description={
        fromSelection
          ? `${ids.length} selected document${ids.length === 1 ? "" : "s"}. A batch follows them from processing to dispatch and export.`
          : "Group documents to follow them together: progress, confidence, schema health, dispatch lanes and export."
      }
      onClose={onClose}
      busy={submit.isPending}
      wide={!fromSelection}
      footer={
        <>
          <button type="button" className="fp-btn fp-btn-secondary" onClick={onClose} disabled={submit.isPending}>
            Cancel
          </button>
          <button
            type="button"
            className="fp-btn fp-btn-primary"
            disabled={!canSubmit || submit.isPending}
            onClick={() => submit.mutate()}
          >
            {submit.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Layers className="h-4 w-4" aria-hidden />}
            {mode === "existing" ? "Add to batch" : `Create batch${ids.length ? ` (${ids.length})` : ""}`}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {fromSelection && existingOptions.length > 0 ? (
          <div className="flex rounded-lg border border-border p-0.5 text-xs font-medium" role="radiogroup" aria-label="Batch">
            {(["new", "existing"] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={mode === value}
                onClick={() => setMode(value)}
                className={`flex-1 rounded-md px-3 py-1.5 ${mode === value ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
              >
                {value === "new" ? "New batch" : "Existing batch"}
              </button>
            ))}
          </div>
        ) : null}

        {mode === "existing" ? (
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Batch</span>
            <select className="fp-input" value={existing} onChange={(e) => setExisting(e.target.value)}>
              <option value="">Choose a batch…</option>
              {existingOptions.map((batch) => (
                <option key={batch.id} value={batch.id}>
                  {batch.name} ({batch.progress.documents} documents)
                </option>
              ))}
            </select>
          </label>
        ) : (
          <>
            <label className="block space-y-1.5">
              <span className="text-sm font-medium">Name</span>
              <input
                className="fp-input"
                value={name}
                maxLength={120}
                placeholder="e.g. October supplier invoices"
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-sm font-medium">Description <span className="font-normal text-muted-foreground">(optional)</span></span>
              <textarea
                className="fp-input min-h-[3.5rem]"
                value={description}
                maxLength={2000}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
          </>
        )}

        {!fromSelection ? (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Documents</legend>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <input
                className="fp-input h-8 pl-8 text-sm"
                placeholder="Filter by file name"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                aria-label="Filter documents"
              />
            </div>
            <div className="max-h-64 overflow-y-auto rounded-lg border border-border">
              {documents.isLoading ? (
                <div className="p-4"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" /></div>
              ) : rows.length === 0 ? (
                <p className="p-4 text-center text-xs text-muted-foreground">No documents match.</p>
              ) : (
                <ul className="divide-y divide-border/60 text-sm">
                  <li className="sticky top-0 z-[1] bg-muted/80 px-3 py-1.5 backdrop-blur">
                    <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-primary"
                        checked={allPicked}
                        onChange={(e) =>
                          setPicked((current) =>
                            e.target.checked
                              ? Array.from(new Set([...current, ...rows.map((r) => r.id)]))
                              : current.filter((id) => !rows.some((r) => r.id === id)),
                          )
                        }
                      />
                      Select all {rows.length} shown
                    </label>
                  </li>
                  {rows.map((row) => (
                    <li key={row.id} className="px-3 py-1.5 hover:bg-muted/40">
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-primary"
                          checked={picked.includes(row.id)}
                          onChange={(e) =>
                            setPicked((current) =>
                              e.target.checked ? [...current, row.id] : current.filter((id) => id !== row.id),
                            )
                          }
                        />
                        <span className="min-w-0 flex-1 truncate">{row.original_filename}</span>
                        <span className="shrink-0 text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                          {row.status.toLowerCase()}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="fp-num text-xs text-muted-foreground">{picked.length} selected</p>
          </fieldset>
        ) : null}
      </div>
    </Modal>
  );
};

export default NewBatchDialog;
