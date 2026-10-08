/**
 * N-020 item 6 — the page itself, beside what was read from it.
 *
 * Renders one page of the document as an image (the API renders PDFs with
 * PDFium; images are served as they are) and draws a box wherever an extracted
 * value is printed. The boxes come from the line geometry the extraction
 * stored (`/evidence`); nothing is re-read. The active field's box is drawn
 * solid, the others faint, so "where does this value come from?" is one glance.
 *
 * Phase 1 workbench: the page opens fitted to the pane's width; zoom runs from
 * 50% to 300% (buttons, Ctrl/⌘ + wheel, or + / − / 0 with the page focused);
 * a zoomed page pans by dragging; ← / → turn pages, and a page number can be
 * typed; choosing a field scrolls its box into view.
 *
 * A document with no page images (Word, text, e-mail) shows its extracted text
 * instead, which is what was read from it.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, FileText, Keyboard, Loader2, Maximize2, Minus, Plus } from "lucide-react";

import {
  getDocumentEvidence,
  getDocumentPageImage,
  getDocumentText,
  type DocumentEvidence,
  type EvidenceLocation,
} from "@/services/api/documentEvidence";
import { useImageFallback } from "@/hooks/useImageFallback";

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
  /** Tailwind max-height for the page canvas (the workbench sets it to the viewport). */
  readonly canvasClassName?: string;
}

/** Percent of the pane's width; 100 is "fit width". */
const ZOOM_STEPS = [50, 75, 100, 125, 150, 200, 250, 300] as const;
const FIT = ZOOM_STEPS.indexOf(100);

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
  canvasClassName = "max-h-[75vh]",
}) => {
  const evidence = useDocumentEvidence(workspaceId, workItemId);
  const renderable = evidence.data?.renderable ?? false;
  const pageCount = Math.max(evidence.data?.pages.length ?? 0, 1);
  const [zoomIndex, setZoomIndex] = useState(FIT);
  const [pageDraft, setPageDraft] = useState(String(page));
  const [showKeys, setShowKeys] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => setPageDraft(String(page)), [page]);

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
  // F-143: a page image the browser cannot decode says so instead of a broken-image icon.
  const pageImage = useImageFallback(imageUrl);
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

  const zoomBy = useCallback((delta: number) => {
    setZoomIndex((index) => Math.min(ZOOM_STEPS.length - 1, Math.max(0, index + delta)));
  }, []);
  const turn = useCallback(
    (delta: number) => onPageChange(Math.min(pageCount, Math.max(1, page + delta))),
    [onPageChange, page, pageCount],
  );

  // Ctrl/⌘ + wheel zooms the page instead of the browser.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return undefined;
    }
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) {
        return;
      }
      event.preventDefault();
      zoomBy(event.deltaY < 0 ? 1 : -1);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomBy, renderable, imageUrl]);

  // Choosing a field brings its box into view.
  useEffect(() => {
    if (!activeField || !canvasRef.current) {
      return;
    }
    const target = canvasRef.current.querySelector<HTMLElement>(`[data-field="${CSS.escape(activeField)}"]`);
    target?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [activeField, page, imageUrl, zoomIndex]);

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
        <div className={`${canvasClassName} overflow-auto p-3`}>
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
  const pannable = zoom > 100;

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) {
      return;
    }
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      zoomBy(1);
    } else if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      zoomBy(-1);
    } else if (event.key === "0") {
      event.preventDefault();
      setZoomIndex(FIT);
    } else if (event.key === "ArrowRight" || event.key === "PageDown") {
      event.preventDefault();
      turn(1);
    } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
      event.preventDefault();
      turn(-1);
    }
  };

  const commitPage = () => {
    const wanted = Number.parseInt(pageDraft, 10);
    if (Number.isFinite(wanted) && wanted >= 1 && wanted <= pageCount) {
      onPageChange(wanted);
    } else {
      setPageDraft(String(page));
    }
  };

  return (
    <section aria-label="Document pages" className="overflow-hidden rounded-xl border border-border bg-card shadow-elevation-1">
      <header className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-2.5 py-1.5">
        <div className="flex items-center gap-0.5 rounded-lg border border-border bg-card p-0.5 shadow-elevation-1">
          <button type="button" onClick={() => turn(-1)} disabled={page <= 1} aria-label="Previous page" className={TOOL}>
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="flex items-center gap-1 px-1 text-xs font-medium tabular-nums text-foreground" aria-live="polite">
            <span className="sr-only">Page {page} of {pageCount}</span>
            <span aria-hidden>Page</span>
            {pageCount > 1 ? (
              <input
                value={pageDraft}
                onChange={(event) => setPageDraft(event.target.value.replace(/\D/g, ""))}
                onBlur={commitPage}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    commitPage();
                  }
                }}
                inputMode="numeric"
                aria-label="Go to page"
                className="h-6 w-9 rounded border border-border bg-background px-1 text-center text-xs tabular-nums"
              />
            ) : (
              <span aria-hidden>{page}</span>
            )}
            <span aria-hidden>of {pageCount}</span>
          </span>
          <button type="button" onClick={() => turn(1)} disabled={page >= pageCount} aria-label="Next page" className={TOOL}>
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="flex items-center gap-0.5 rounded-lg border border-border bg-card p-0.5 shadow-elevation-1">
            <button type="button" onClick={() => zoomBy(-1)} disabled={zoomIndex === 0} aria-label="Zoom out" className={TOOL}>
              <Minus className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setZoomIndex(FIT)}
              title="Fit to width (0)"
              aria-label={`Zoom ${zoom}%, fit to width`}
              className="w-12 rounded-md py-1 text-center text-xs font-medium tabular-nums text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              {zoom}%
            </button>
            <button
              type="button"
              onClick={() => zoomBy(1)}
              disabled={zoomIndex === ZOOM_STEPS.length - 1}
              aria-label="Zoom in"
              className={TOOL}
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
            <button type="button" onClick={() => setZoomIndex(FIT)} aria-label="Fit to width" title="Fit to width" className={TOOL} disabled={zoomIndex === FIT}>
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="relative">
            <button
              type="button"
              aria-label="Keyboard shortcuts"
              aria-expanded={showKeys}
              onClick={() => setShowKeys((open) => !open)}
              className={`${TOOL} border border-border bg-card shadow-elevation-1`}
            >
              <Keyboard className="h-3.5 w-3.5" />
            </button>
            {showKeys ? (
              <div role="note" className="absolute right-0 top-9 z-20 w-56 rounded-lg border border-border bg-popover p-3 text-xs text-popover-foreground shadow-elevation-2">
                <p className="mb-1.5 font-semibold">With the page focused</p>
                <ul className="space-y-1 text-muted-foreground">
                  <li><kbd className="font-mono">+</kbd> / <kbd className="font-mono">−</kbd> zoom · <kbd className="font-mono">0</kbd> fit width</li>
                  <li><kbd className="font-mono">←</kbd> / <kbd className="font-mono">→</kbd> previous / next page</li>
                  <li>Ctrl/⌘ + wheel to zoom · drag to pan when zoomed</li>
                </ul>
              </div>
            ) : null}
          </div>
        </div>
      </header>
      <div
        ref={canvasRef}
        tabIndex={0}
        role="group"
        aria-label={`Page ${page} canvas. Plus and minus zoom, arrows turn pages.`}
        onKeyDown={onKeyDown}
        onMouseDown={(event) => {
          if (!pannable || event.button !== 0 || (event.target as HTMLElement).closest("button")) {
            return;
          }
          const canvas = canvasRef.current;
          if (!canvas) {
            return;
          }
          drag.current = { x: event.clientX, y: event.clientY, left: canvas.scrollLeft, top: canvas.scrollTop };
          setDragging(true);
        }}
        onMouseMove={(event) => {
          const canvas = canvasRef.current;
          if (!drag.current || !canvas) {
            return;
          }
          canvas.scrollLeft = drag.current.left - (event.clientX - drag.current.x);
          canvas.scrollTop = drag.current.top - (event.clientY - drag.current.y);
        }}
        onMouseUp={() => {
          drag.current = null;
          setDragging(false);
        }}
        onMouseLeave={() => {
          drag.current = null;
          setDragging(false);
        }}
        className={`fp-viewer-canvas ${canvasClassName} overflow-auto p-4 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/50 sm:p-6 ${
          pannable ? (dragging ? "cursor-grabbing select-none" : "cursor-grab") : ""
        }`}
      >
        <div className="relative mx-auto rounded-sm bg-white shadow-elevation-3 ring-1 ring-black/5 transition-[width] duration-150" style={{ width: `${zoom}%` }}>
          {imageUrl && !pageImage.failed ? (
            <img
              src={imageUrl}
              alt={`Page ${page} of the document`}
              className="block w-full select-none"
              draggable={false}
              onError={pageImage.onError}
            />
          ) : (
            <div className="flex h-96 items-center justify-center text-xs text-muted-foreground">
              {image.isError || pageImage.failed ? "This page couldn't be rendered." : <Loader2 className="h-4 w-4 animate-spin" />}
            </div>
          )}
          {imageUrl && !pageImage.failed &&
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
