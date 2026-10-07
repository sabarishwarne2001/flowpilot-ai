/**
 * N-020 item 6 — the page itself, beside what was read from it.
 *
 * Renders one page of the document as an image (the API renders PDFs with
 * PDFium; images are served as they are) and draws a box wherever an extracted
 * value is printed. The boxes come from the line geometry the extraction
 * stored (`/evidence`); nothing is re-read. The active field's box is drawn
 * solid, the others faint, so "where does this value come from?" is one glance.
 *
 * A document with no page images (Word, text, e-mail) shows its extracted text
 * instead, which is what was read from it.
 */

import React, { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, FileText, Loader2, Minus, Plus } from "lucide-react";

import {
  getDocumentEvidence,
  getDocumentPageImage,
  getDocumentText,
  type DocumentEvidence,
  type EvidenceLocation,
} from "@/services/api/documentEvidence";

export const evidenceKey = (workspaceId: string, workItemId: string) =>
  ["document-evidence", workspaceId, workItemId, "extracted"] as const;

export const useDocumentEvidence = (workspaceId: string, workItemId: string, enabled = true) =>
  useQuery<DocumentEvidence>({
    queryKey: evidenceKey(workspaceId, workItemId),
    queryFn: () => getDocumentEvidence(workspaceId, workItemId, []),
    enabled: Boolean(workspaceId && workItemId) && enabled,
    staleTime: 60_000,
  });

interface DocumentPageViewerProps {
  readonly workspaceId: string;
  readonly workItemId: string;
  readonly page: number;
  readonly onPageChange: (page: number) => void;
  /** Boxes to draw; `activeField` picks the emphasised ones. */
  readonly locations: readonly EvidenceLocation[];
  readonly activeField?: string | null;
  readonly onSelectField?: (field: string) => void;
}

const ZOOM_STEPS = [60, 80, 100, 125, 150, 200] as const;

/** One colour per field name, stable across renders and pages. */
const BOX_TONES = [
  "border-sky-500/80 bg-sky-400/10 hover:bg-sky-400/25",
  "border-violet-500/80 bg-violet-400/10 hover:bg-violet-400/25",
  "border-amber-500/80 bg-amber-300/10 hover:bg-amber-300/25",
  "border-emerald-500/80 bg-emerald-400/10 hover:bg-emerald-400/25",
  "border-rose-500/80 bg-rose-400/10 hover:bg-rose-400/25",
  "border-cyan-500/80 bg-cyan-400/10 hover:bg-cyan-400/25",
] as const;

const toneFor = (field: string | null | undefined): string => {
  const key = field ?? "";
  let hash = 0;
  for (let index = 0; index < key.length; index += 1) {
    hash = (hash * 31 + key.charCodeAt(index)) >>> 0;
  }
  return BOX_TONES[hash % BOX_TONES.length] ?? BOX_TONES[0];
};

const TOOL =
  "flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-35";

export const DocumentPageViewer: React.FC<DocumentPageViewerProps> = ({
  workspaceId,
  workItemId,
  page,
  onPageChange,
  locations,
  activeField = null,
  onSelectField,
}) => {
  const evidence = useDocumentEvidence(workspaceId, workItemId);
  const renderable = evidence.data?.renderable ?? false;
  const pageCount = Math.max(evidence.data?.pages.length ?? 0, 1);
  const [zoomIndex, setZoomIndex] = useState(2);

  const image = useQuery({
    queryKey: ["document-page", workspaceId, workItemId, page],
    queryFn: () => getDocumentPageImage(workspaceId, workItemId, page),
    enabled: renderable,
    staleTime: 300_000,
  });
  const text = useQuery({
    queryKey: ["document-text", workspaceId, workItemId],
    queryFn: () => getDocumentText(workspaceId, workItemId),
    enabled: evidence.isSuccess && !renderable,
    staleTime: 300_000,
  });

  const [imageUrl, setImageUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!image.data) {
      setImageUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(image.data);
    setImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image.data]);

  const boxes = useMemo(() => locations.filter((location) => location.page === page), [locations, page]);

  if (evidence.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-border bg-muted/20 text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading the document…
      </div>
    );
  }

  if (evidence.isError) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-border bg-muted/20 p-4 text-center text-sm text-muted-foreground">
        The document preview couldn&apos;t be loaded. Its extracted data is still shown.
      </div>
    );
  }

  if (!renderable) {
    return (
      <section aria-label="Document text" className="rounded-lg border border-border bg-card">
        <header className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs font-semibold text-muted-foreground">
          <FileText className="h-3.5 w-3.5" aria-hidden /> Text read from the document
        </header>
        <div className="max-h-[70vh] overflow-auto p-3">
          {text.isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : text.data?.text ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5">{text.data.text}</pre>
          ) : (
            <p className="text-sm text-muted-foreground">No text was read from this document.</p>
          )}
          {text.data?.truncated && (
            <p className="mt-2 text-xs text-muted-foreground">
              Showing the first {text.data.text.length.toLocaleString()} of {text.data.characters.toLocaleString()} characters.
            </p>
          )}
        </div>
      </section>
    );
  }

  const zoom = ZOOM_STEPS[zoomIndex] ?? 100;

  return (
    <section aria-label="Document pages" className="overflow-hidden rounded-xl border border-border bg-card shadow-elevation-1">
      <header className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-2.5 py-1.5">
        <div className="flex items-center gap-0.5 rounded-lg border border-border bg-card p-0.5 shadow-elevation-1">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          aria-label="Previous page"
          className={TOOL}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="px-1.5 text-xs font-medium tabular-nums text-foreground" aria-live="polite">
          Page {page} of {pageCount}
        </span>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(pageCount, page + 1))}
          disabled={page >= pageCount}
          aria-label="Next page"
          className={TOOL}
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        </div>
        <div className="ml-auto flex items-center gap-0.5 rounded-lg border border-border bg-card p-0.5 shadow-elevation-1">
          <button
            type="button"
            onClick={() => setZoomIndex((index) => Math.max(0, index - 1))}
            disabled={zoomIndex === 0}
            aria-label="Zoom out"
            className={TOOL}
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setZoomIndex(Math.max(0, ZOOM_STEPS.indexOf(100)))}
            title="Reset zoom"
            className="w-12 rounded-md py-1 text-center text-xs font-medium tabular-nums text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {zoom}%
          </button>
          <button
            type="button"
            onClick={() => setZoomIndex((index) => Math.min(ZOOM_STEPS.length - 1, index + 1))}
            disabled={zoomIndex === ZOOM_STEPS.length - 1}
            aria-label="Zoom in"
            className={TOOL}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>
      <div className="fp-viewer-canvas max-h-[75vh] overflow-auto p-4 sm:p-6">
        <div className="relative mx-auto rounded-sm bg-white shadow-elevation-3 ring-1 ring-black/5" style={{ width: `${zoom}%` }}>
          {imageUrl ? (
            <img src={imageUrl} alt={`Page ${page} of the document`} className="block w-full select-none" draggable={false} />
          ) : (
            <div className="flex h-96 items-center justify-center text-xs text-muted-foreground">
              {image.isError ? "This page couldn't be rendered." : <Loader2 className="h-4 w-4 animate-spin" />}
            </div>
          )}
          {imageUrl &&
            boxes.map((box, index) => {
              const active = activeField !== null && box.field === activeField;
              return (
                <button
                  key={`${box.field ?? "value"}-${index}`}
                  type="button"
                  tabIndex={-1}
                  onClick={() => box.field && onSelectField?.(box.field)}
                  title={`${box.field ?? "Value"}: ${box.value}`}
                  data-field={box.field ?? undefined}
                  data-active={active ? "true" : "false"}
                  className={`absolute rounded-[3px] border-[1.5px] transition-all duration-150 ${
                    active
                      ? "z-[1] border-primary bg-primary/20 shadow-[0_0_0_3px_hsl(var(--primary)/0.25),0_0_18px_hsl(var(--primary)/0.45)]"
                      : toneFor(box.field)
                  }`}
                  style={{
                    left: `${box.x0 * 100}%`,
                    top: `${box.y0 * 100}%`,
                    width: `${Math.max(box.x1 - box.x0, 0.004) * 100}%`,
                    height: `${Math.max(box.y1 - box.y0, 0.004) * 100}%`,
                  }}
                />
              );
            })}
        </div>
      </div>
    </section>
  );
};

export default DocumentPageViewer;
