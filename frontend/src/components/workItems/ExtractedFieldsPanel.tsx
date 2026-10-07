/**
 * N-020 item 7 — the extracted fields, each correctable in place.
 *
 * Plain values (text, numbers, true/false) get an Edit button for anyone who
 * may edit content (CONTRIBUTOR and up) on a finished document. Saving calls
 * `PATCH …/fields`; the server refuses while the document waits in the review
 * queue or sits under a legal hold, and the toast says so in its own words.
 * Tables and grouped values are listed read-only: they are corrected in Tables.
 *
 * Each field says where it is printed ("p. 2", click to show it on the page) or
 * that it is not printed in a comparable form, and a corrected field carries
 * a "Corrected" badge with what it said before.
 */

import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Check, History, Loader2, MapPin, Pencil, X } from "lucide-react";

import {
  correctFields,
  getFieldHistory,
  type EvidenceLocation,
  type FieldCorrection,
} from "@/services/api/documentEvidence";
import { ApiError } from "@/services/api/client";
import { workItemKeys } from "@/services/api/queryKeys";
import { formatDateTime } from "@/utils/formatters";
import { evidenceKey } from "@/components/workItems/DocumentPageViewer";

type Plain = string | number | boolean | null;

const isPlain = (value: unknown): value is Plain =>
  value === null || ["string", "number", "boolean"].includes(typeof value);

/** "invoice_number" -> "Invoice number"; "document_classification" -> "Document type". */
export const fieldLabel = (key: string): string => {
  if (key === "document_classification") {
    return "Document type";
  }
  const words = key.replace(/[_\-.]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const show = (value: unknown): string => {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  return typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value);
};

/** What the person typed, in the type the field already had. */
const parseInput = (raw: string, previous: Plain): Plain => {
  const text = raw.trim();
  if (text === "") {
    return null;
  }
  if (typeof previous === "number") {
    const number = Number(text.replace(/,/g, ""));
    return Number.isFinite(number) ? number : text;
  }
  if (typeof previous === "boolean") {
    if (/^(yes|true|1)$/i.test(text)) {return true;}
    if (/^(no|false|0)$/i.test(text)) {return false;}
  }
  return text;
};

interface ExtractedFieldsPanelProps {
  readonly workspaceId: string;
  readonly workItemId: string;
  readonly entities: Record<string, unknown> | null | undefined;
  readonly locations: readonly EvidenceLocation[];
  readonly canEdit: boolean;
  readonly readOnlyReason?: string | null;
  /** Where the person can act instead (the review queue for a document waiting there). */
  readonly readOnlyLink?: { readonly to: string; readonly label: string } | null;
  readonly activeField: string | null;
  readonly onSelectField: (field: string, page: number | null) => void;
}

export const ExtractedFieldsPanel: React.FC<ExtractedFieldsPanelProps> = ({
  workspaceId,
  workItemId,
  entities,
  locations,
  canEdit,
  readOnlyReason = null,
  readOnlyLink = null,
  activeField,
  onSelectField,
}) => {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("");

  const historyKey = ["work-item-field-history", workspaceId, workItemId] as const;
  const history = useQuery({
    queryKey: historyKey,
    queryFn: () => getFieldHistory(workspaceId, workItemId),
    enabled: Boolean(workspaceId && workItemId),
    staleTime: 30_000,
  });

  const latestCorrection = useMemo(() => {
    const map = new Map<string, FieldCorrection>();
    for (const row of history.data ?? []) {
      if (!map.has(row.field_path)) {
        map.set(row.field_path, row);
      }
    }
    return map;
  }, [history.data]);

  const firstPage = useMemo(() => {
    const map = new Map<string, number>();
    for (const location of locations) {
      if (location.field && !map.has(location.field)) {
        map.set(location.field, location.page);
      }
    }
    return map;
  }, [locations]);

  const save = useMutation({
    mutationFn: ({ field, value }: { field: string; value: Plain }) =>
      correctFields(workspaceId, workItemId, { [field]: value }, reason.trim() || undefined),
    onSuccess: async (result, { field }) => {
      setEditing(null);
      setReason("");
      if (result.corrected.length === 0) {
        toast.info(`${fieldLabel(field)} already had that value.`);
        return;
      }
      toast.success(
        result.extraction_memory_fields.includes(field)
          ? `${fieldLabel(field)} corrected. Extraction memory will learn from it.`
          : `${fieldLabel(field)} corrected.`,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: workItemKeys.detail(workspaceId, workItemId) }),
        queryClient.invalidateQueries({ queryKey: historyKey }),
        queryClient.invalidateQueries({ queryKey: evidenceKey(workspaceId, workItemId) }),
        queryClient.invalidateQueries({ queryKey: ["work-item-field-editability", workspaceId, workItemId] }),
      ]);
    },
    onError: (error) => {
      toast.error(error instanceof ApiError && error.message ? error.message : "The correction couldn't be saved.");
    },
  });

  const entries = Object.entries(entities ?? {});
  const plain = entries.filter(([, value]) => isPlain(value));
  const grouped = entries.filter(([, value]) => !isPlain(value));

  if (entries.length === 0) {
    return (
      <section aria-label="Extracted fields" className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-bold">Extracted fields</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          No fields were extracted from this document. Check the AI provider in Settings → AI, then reprocess it.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Extracted fields" className="rounded-lg border border-border bg-card">
      <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h2 className="text-sm font-bold">Extracted fields</h2>
        {history.data && history.data.length > 0 && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <History className="h-3.5 w-3.5" aria-hidden />
            {history.data.length} correction{history.data.length === 1 ? "" : "s"}
          </span>
        )}
      </header>
      {!canEdit && readOnlyReason && (
        <p role="note" className="border-b border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
          {readOnlyReason}
          {readOnlyLink && (
            <>
              {" "}
              <Link to={readOnlyLink.to} className="font-semibold text-primary hover:underline">
                {readOnlyLink.label}
              </Link>
            </>
          )}
        </p>
      )}
      <ul className="divide-y divide-border">
        {plain.map(([key, value]) => {
          const page = firstPage.get(key) ?? null;
          const correction = latestCorrection.get(key);
          const isEditing = editing === key;
          const active = activeField === key;
          return (
            <li
              key={key}
              className={`px-4 py-3 ${active ? "bg-primary/5" : ""}`}
              data-field={key}
            >
              <div className="flex items-start gap-3">
                <button
                  type="button"
                  onClick={() => onSelectField(key, page)}
                  className="min-w-0 flex-1 text-left"
                  aria-label={`Show ${fieldLabel(key)} on the page`}
                >
                  <span className="block text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
                    {fieldLabel(key)}
                  </span>
                  {!isEditing && (
                    <span className="mt-0.5 block break-words text-sm font-semibold text-foreground">{show(value)}</span>
                  )}
                </button>
                {!isEditing && (
                  <div className="flex flex-shrink-0 items-center gap-1.5">
                    {correction && (
                      <span
                        className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold text-emerald-700 dark:text-emerald-400"
                        title={`Was ${show(correction.previous_value)} — corrected ${formatDateTime(correction.created_at)}${
                          correction.reason ? ` (${correction.reason})` : ""
                        }`}
                      >
                        Corrected
                      </span>
                    )}
                    {page !== null ? (
                      <button
                        type="button"
                        onClick={() => onSelectField(key, page)}
                        className="inline-flex items-center gap-0.5 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
                        title="Show where this value is printed"
                      >
                        <MapPin className="h-3 w-3" aria-hidden />
                        p. {page}
                      </button>
                    ) : (
                      value !== null && (
                        <span className="text-[10px] text-muted-foreground" title="Not printed on the page in this form">
                          not on page
                        </span>
                      )
                    )}
                    {canEdit && (
                      <button
                        type="button"
                        onClick={() => {
                          setEditing(key);
                          setDraft(value === null ? "" : String(value));
                          setReason("");
                          onSelectField(key, page);
                        }}
                        aria-label={`Edit field ${fieldLabel(key)}`}
                        className="rounded border border-border p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                )}
              </div>
              {isEditing && (
                <form
                  className="mt-2 space-y-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    save.mutate({ field: key, value: parseInput(draft, value as Plain) });
                  }}
                >
                  <input
                    autoFocus
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    aria-label={`New value for ${fieldLabel(key)}`}
                    maxLength={2000}
                    className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <input
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    aria-label="Reason for the correction (optional)"
                    placeholder="Reason (optional), e.g. the model read the PO number"
                    maxLength={500}
                    className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      type="submit"
                      disabled={save.isPending}
                      className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
                    >
                      {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                      Save correction
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(null)}
                      disabled={save.isPending}
                      className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
                    >
                      <X className="h-3.5 w-3.5" /> Cancel
                    </button>
                    {correction && (
                      <span className="text-[11px] text-muted-foreground">Was {show(correction.previous_value)}</span>
                    )}
                  </div>
                </form>
              )}
            </li>
          );
        })}
      </ul>
      {grouped.length > 0 && (
        <details className="border-t border-border px-4 py-3">
          <summary className="cursor-pointer text-xs font-semibold text-muted-foreground">
            {grouped.length} grouped value{grouped.length === 1 ? "" : "s"} (tables, line items) — corrected in Tables
          </summary>
          <ul className="mt-2 space-y-2">
            {grouped.map(([key, value]) => (
              <li key={key}>
                <span className="block text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
                  {fieldLabel(key)}
                </span>
                <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/30 p-2 text-[11px] leading-5">
                  {JSON.stringify(value, null, 2)}
                </pre>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
};

export default ExtractedFieldsPanel;
