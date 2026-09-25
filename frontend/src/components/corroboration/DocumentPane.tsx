/**
 * ARCH45-S2:document-pane — one document of a comparison, one page at a time.
 *
 * The page image is rendered server-side (PDFium) and fetched WITH THE SESSION
 * through useAuthorizedBlobUrl (a bare <img src> would carry no credentials
 * and the endpoint sends no-store: it is a rendering of the document). The
 * evidence boxes of the selected difference are drawn over it; boxes are in
 * the pipeline's 200-DPI page pixels, so they are placed as percentages of the
 * stored page size and stay aligned at any zoom. A document with no page
 * images (a text upload) shows its value for the selected difference instead.
 */
import React from "react";
import { ChevronLeft, ChevronRight, FileText, Loader2 } from "lucide-react";

import { useAuthorizedBlobUrl } from "@/hooks/useAuthorizedBlobUrl";
import { pageImagePath } from "@/services/api/corroboration";
import type { EvidenceSpan, RunDocument, Severity } from "@/types/corroboration";

const BOX_TONE: Readonly<Record<Severity, string>> = {
  HIGH: "border-red-600 bg-red-500/15",
  MEDIUM: "border-amber-500 bg-amber-400/15",
  LOW: "border-slate-500 bg-slate-400/10",
};

interface DocumentPaneProps {
  readonly workspaceId: string;
  readonly runId: string;
  readonly document: RunDocument;
  readonly page: number;
  readonly onPage: (page: number) => void;
  readonly spans: readonly EvidenceSpan[];
  readonly severity: Severity;
  readonly fallbackText: string | null;
  readonly baseline: boolean;
}

export const DocumentPane: React.FC<DocumentPaneProps> = ({
  workspaceId, runId, document, page, onPage, spans, severity, fallbackText, baseline,
}) => {
  const pages = Math.max(document.pages.length, document.page_count ?? 0, 1);
  const geometry = document.pages.find((p) => p.page === page);
  const blob = useAuthorizedBlobUrl(
    document.renderable ? pageImagePath(workspaceId, runId, document.work_item_id, page) : null,
  );
  const onThisPage = spans.filter((s) => s.page === page && s.bbox);
  const otherPages = [...new Set(spans.filter((s) => s.page !== page).map((s) => s.page))].sort((a, b) => a - b);
  return (
    <section className="flex min-w-0 flex-1 flex-col rounded-lg border border-border/60 bg-card" aria-label={document.label}>
      <header className="flex items-center gap-2 border-b border-border/60 px-2 py-1.5">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary">
          {document.position + 1}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs font-semibold" title={document.label}>
          {document.label}
          {baseline ? <span className="ml-1 font-normal text-muted-foreground">(baseline)</span> : null}
        </span>
        {document.changed_since ? (
          <span className="rounded bg-amber-500/15 px-1.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
            changed since
          </span>
        ) : null}
        <button type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}
          className="rounded p-0.5 hover:bg-muted disabled:opacity-40">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="text-[11px] tabular-nums text-muted-foreground">{page} / {pages}</span>
        <button type="button" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}
          className="rounded p-0.5 hover:bg-muted disabled:opacity-40">
          <ChevronRight className="h-4 w-4" />
        </button>
      </header>
      {otherPages.length > 0 ? (
        <p className="border-b border-border/40 px-2 py-1 text-[11px] text-muted-foreground">
          Also on page{otherPages.length > 1 ? "s" : ""}{" "}
          {otherPages.map((p) => (
            <button key={p} type="button" className="mr-1 underline" onClick={() => onPage(p)}>{p}</button>
          ))}
        </p>
      ) : null}
      <div className="relative min-h-[16rem] flex-1 overflow-auto bg-muted/20 p-2">
        {document.renderable && geometry && geometry.width > 0 && geometry.height > 0 ? (
          <div className="relative mx-auto w-full max-w-[52rem]">
            {blob.url ? (
              <img src={blob.url} alt={`${document.label}, page ${page}`} className="block w-full select-none shadow" draggable={false} />
            ) : (
              <div className="aspect-[1/1.3] w-full animate-pulse rounded bg-muted" />
            )}
            {onThisPage.map((span, index) => {
              const b = span.bbox!;
              return (
                <div
                  key={`${index}-${b.x0}-${b.y0}`}
                  className={`pointer-events-none absolute rounded-sm border-2 ${BOX_TONE[severity]}`}
                  style={{
                    left: `${(b.x0 / geometry.width) * 100}%`, top: `${(b.y0 / geometry.height) * 100}%`,
                    width: `${((b.x1 - b.x0) / geometry.width) * 100}%`, height: `${((b.y1 - b.y0) / geometry.height) * 100}%`,
                  }}
                  title={span.text}
                />
              );
            })}
            {blob.status === "loading" ? (
              <Loader2 className="absolute right-2 top-2 h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading page" />
            ) : null}
            {blob.status === "error" ? <p className="mt-2 text-xs text-destructive">{blob.error}</p> : null}
          </div>
        ) : (
          <div className="space-y-2 p-2 text-xs">
            <p className="flex items-center gap-1 text-muted-foreground">
              <FileText className="h-3.5 w-3.5" aria-hidden /> No page image for this document; its text is shown.
            </p>
            <p className="whitespace-pre-wrap break-words">{fallbackText ?? "Select a difference to see this document's text."}</p>
          </div>
        )}
      </div>
    </section>
  );
};

export default DocumentPane;
