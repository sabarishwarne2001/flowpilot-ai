/**
 * ARCH44-S2:document-tables — the tables found in one document, on Work Item
 * details: each links to the viewer, the whole set downloads as one workbook,
 * and a contributor can (re-)extract. Renders nothing without
 * capability.table_intelligence.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Download, Loader2, Table2 } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { HINT, SURFACE_INSET } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { tablePath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { downloadDocumentTables, extractDocumentTables, getDocumentTables, tableKeys } from "@/services/api/tables";
import { STATUS_LABELS, STATUS_TONE } from "@/types/tables";

export const DocumentTables: React.FC<{ readonly workItemId: string }> = ({ workItemId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.tableIntelligence);
  const canEdit = workspace?.role === "ADMIN" || workspace?.role === "OWNER" || workspace?.role === "CONTRIBUTOR";
  const client = useQueryClient();
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const query = useQuery({
    queryKey: tableKeys.document(workspaceId, workItemId),
    queryFn: () => getDocumentTables(workspaceId, workItemId),
    enabled: Boolean(workspaceId && workItemId && capability.granted),
  });
  const extract = useMutation({
    mutationFn: (force: boolean) => extractDocumentTables(workspaceId, workItemId, force),
    onSuccess: () => void client.invalidateQueries({ queryKey: tableKeys.all(workspaceId) }),
  });
  if (!capability.granted || !query.data) {
    return null;
  }
  const data = query.data;
  const corrected = data.tables.some((t) => t.corrected_at || t.status === "REVIEWED" || t.status === "REJECTED");
  const workbook = async (): Promise<void> => {
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadDocumentTables(workspaceId, workItemId, "xlsx", data.original_filename);
    } catch (error) {
      setDownloadError(errorMessage(error, "The download failed."));
    } finally {
      setDownloading(false);
    }
  };
  return (
    <section className={`${SURFACE_INSET} space-y-2 p-3`} aria-labelledby="document-tables-title">
      <div className="flex flex-wrap items-center gap-2">
        <Table2 className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h3 id="document-tables-title" className="text-sm font-semibold">Tables in this document</h3>
        <div className="ml-auto flex items-center gap-3">
          {data.tables.length > 0 ? (
            <button type="button" onClick={() => void workbook()} disabled={downloading}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline disabled:opacity-50">
              {downloading ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : <Download className="h-3 w-3" aria-hidden />}
              All tables (XLSX)
            </button>
          ) : null}
          {canEdit && data.extractable ? (
            <button type="button" onClick={() => extract.mutate(corrected)} disabled={extract.isPending}
                    className="text-xs font-semibold text-primary hover:underline disabled:opacity-50"
                    title={corrected ? "Replaces corrected tables" : undefined}>
              {extract.isPending ? <Loader2 className="inline h-3 w-3 animate-spin" aria-hidden /> : data.tables.length ? "Re-extract" : "Extract tables"}
            </button>
          ) : null}
        </div>
      </div>
      {extract.data?.ran === "QUEUED" ? <p className={HINT}>Queued — long documents are extracted in the background.</p> : null}
      {extract.error ? <p className="text-xs text-destructive">{errorMessage(extract.error, "Extraction failed.")}</p> : null}
      {downloadError ? <p className="text-xs text-destructive">{downloadError}</p> : null}
      {data.tables.length === 0 ? (
        <p className={HINT}>{data.extractable ? "No tables were found in this document." : "Tables are extracted from PDFs and images."}</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {data.tables.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center gap-2">
              <Link className="underline" to={tablePath(orgSlug, workspaceSlug, t.id)}>
                Table {t.ordinal + 1}{t.title ? ` — ${t.title}` : ""}
              </Link>
              <span className="text-xs text-muted-foreground">
                {t.page_start === t.page_end ? `p${t.page_start}` : `p${t.page_start}–${t.page_end}`} · {t.n_rows - t.header_rows} × {t.n_cols}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_TONE[t.status]}`}>{STATUS_LABELS[t.status]}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default DocumentTables;
