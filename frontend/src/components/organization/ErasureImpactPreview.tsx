import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2, ScanSearch, ShieldCheck } from "lucide-react";

import { previewErasure } from "@/services/api/compliance";
import { complianceKeys } from "@/services/api/queryKeys";
import { totalDestroyed } from "@/types/compliance";

const TABLE_LABELS: Readonly<Record<string, string>> = {
  work_items: "Documents",
  work_item_chunks: "Retrieval chunks",
  conversations: "Assistant conversations",
  conversation_messages: "Assistant messages",
  uploaded_files: "Uploaded files",
  notifications: "Notifications",
  sessions: "Active sessions",
  api_keys: "API keys",
};

const labelFor = (table: string): string =>
  TABLE_LABELS[table] ??
  table.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const errorMessage = (error: unknown): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    return first?.msg ?? "The preview was rejected.";
  }
  return "The preview could not be run. Check the subject user ID and try again.";
};

export interface ErasureImpactPreviewProps {
  readonly organizationId: string;
  readonly subjectUserId: string;
  readonly disabled?: boolean;
  readonly onPreviewedSubjectChange: (subjectUserId: string | null) => void;
}

export const ErasureImpactPreview: React.FC<ErasureImpactPreviewProps> = ({
  organizationId,
  subjectUserId,
  disabled = false,
  onPreviewedSubjectChange,
}) => {
  const trimmed = subjectUserId.trim();
  const [requested, setRequested] = useState(false);

  useEffect(() => {
    setRequested(false);
  }, [trimmed]);

  const preview = useQuery({
    queryKey: complianceKeys.erasurePreview(organizationId, trimmed),
    queryFn: () => previewErasure(organizationId, trimmed),
    enabled: requested && trimmed.length > 0,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });

  const data = preview.data;
  const previewMatchesSubject =
    data !== undefined && data.subject_user_id === trimmed;

  useEffect(() => {
    onPreviewedSubjectChange(previewMatchesSubject ? trimmed : null);
  }, [previewMatchesSubject, trimmed, onPreviewedSubjectChange]);

  const counts = data?.counts ?? {};
  const rows = Object.entries(counts).sort(([a], [b]) => a.localeCompare(b));
  const total = data ? totalDestroyed(data.counts) : 0;

  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <ScanSearch
            className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <div>
            <p className="text-sm font-medium text-foreground">
              Impact preview
            </p>
            <p className="text-xs text-muted-foreground">
              A dry run. Nothing is written until you confirm below.
            </p>
          </div>
        </div>
        <button
          type="button"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium disabled:opacity-50"
          disabled={disabled || trimmed.length === 0 || preview.isFetching}
          onClick={() => {
            if (requested) {
              void preview.refetch();
            } else {
              setRequested(true);
            }
          }}
        >
          {preview.isFetching ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : null}
          {previewMatchesSubject ? "Run again" : "Run impact preview"}
        </button>
      </div>

      {trimmed.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Enter a subject user ID to see what erasing them would destroy.
        </p>
      ) : null}

      {preview.isError ? (
        <p className="mt-3 text-xs text-destructive" role="alert">
          {errorMessage(preview.error)}
        </p>
      ) : null}

      {previewMatchesSubject ? (
        <div className="mt-3 space-y-3">
          <div>
            <p className="text-xs font-medium text-foreground">
              Destroyed permanently
            </p>
            {rows.length === 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">
                This subject has no erasable records. The erasure will still be
                recorded so a repeat request is recognised.
              </p>
            ) : (
              <table className="mt-1.5 w-full text-left text-xs">
                <tbody>
                  {rows.map(([table, count]) => (
                    <tr key={table} className="border-t border-border/60">
                      <td className="py-1 pr-4 text-muted-foreground">
                        {labelFor(table)}
                      </td>
                      <td className="py-1 text-right font-medium tabular-nums text-foreground">
                        {count.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                  <tr className="border-t border-border">
                    <td className="py-1 pr-4 font-medium text-foreground">
                      Total records
                    </td>
                    <td className="py-1 text-right font-semibold tabular-nums text-destructive">
                      {total.toLocaleString()}
                    </td>
                  </tr>
                </tbody>
              </table>
            )}
          </div>

          {data.preserved_tables.length > 0 ? (
            <div className="flex items-start gap-2 rounded border border-border bg-background p-2">
              <ShieldCheck
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground"
                aria-hidden
              />
              <div>
                <p className="text-xs font-medium text-foreground">
                  Kept for statutory retention
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {data.preserved_tables.map(labelFor).join(", ")}. Invoices,
                  usage records and the audit trail outrank an erasure request
                  and are never destroyed by one.
                </p>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {requested && !preview.isFetching && !preview.isError && !previewMatchesSubject && trimmed.length > 0 ? (
        <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          The subject ID changed. Run the preview again before erasing.
        </p>
      ) : null}
    </div>
  );
};

export default ErasureImpactPreview;
