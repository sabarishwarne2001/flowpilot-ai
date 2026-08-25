import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AlertTriangle, Loader2, Minus, Plus } from "lucide-react";
import * as pdfjs from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  PDFPageProxy,
} from "pdfjs-dist";

import BboxOverlay from "@/components/pdf/BboxOverlay";
import type { OverlayBox } from "@/components/pdf/BboxOverlay";
import apiClient from "@/services/api/client";
import type { CitationSource } from "@/services/streaming/resumableStream";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const SCALE_STEP = 0.25;
const WINDOW_RADIUS = 2;

export interface PdfViewerProps {
  readonly workspaceId: string;
  readonly workItemId: string;
  readonly sources?: readonly CitationSource[];
  readonly activeChunkId?: string | null;
  readonly onBoxClick?: (source: CitationSource) => void;
  readonly className?: string;
}

interface PageGeometry {
  readonly widthPt: number;
  readonly heightPt: number;
}

export const PdfViewer: React.FC<PdfViewerProps> = ({
  workspaceId,
  workItemId,
  sources = [],
  activeChunkId = null,
  onBoxClick,
  className = "",
}) => {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [geometry, setGeometry] = useState<PageGeometry[]>([]);
  const [scale, setScale] = useState(1.25);
  const [visiblePage, setVisiblePage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  useEffect(() => {
    let cancelled = false;
    let task: PDFDocumentLoadingTask | null = null;

    const load = async () => {
      setLoading(true);
      setError(null);
      setDoc(null);
      setGeometry([]);

      try {
        const response = await apiClient.get<ArrayBuffer>(
          `/workspaces/${encodeURIComponent(workspaceId)}` +
            `/work-items/${encodeURIComponent(workItemId)}/content`,
          {
            responseType: "arraybuffer",
            headers: { Accept: "application/pdf" },
            timeout: 120_000,
          },
        );

        if (cancelled) {
          return;
        }

        const bytes = new Uint8Array(response.data.slice(0));

        task = pdfjs.getDocument({
          data: bytes,
          disableAutoFetch: true,
        });

        const loaded: PDFDocumentProxy = await task.promise;

        if (cancelled) {
          void task.destroy();
          return;
        }

        const measured: PageGeometry[] = [];
        for (let n = 1; n <= loaded.numPages; n += 1) {
          const page = await loaded.getPage(n);
          const viewport = page.getViewport({ scale: 1 });
          measured.push({ widthPt: viewport.width, heightPt: viewport.height });
          page.cleanup();
        }

        if (cancelled) {
          void task.destroy();
          return;
        }

        setDoc(loaded);
        setGeometry(measured);
        setLoading(false);
      } catch (caught) {
        if (cancelled) {
          return;
        }
        setLoading(false);
        setError(
          caught instanceof Error && caught.message
            ? caught.message
            : "This document could not be opened.",
        );
      }
    };

    void load();

    return () => {
      cancelled = true;
      if (task) {
        void task.destroy();
      }
    };
  }, [workspaceId, workItemId]);

  const sourcesByPage = useMemo(() => {
    const map = new Map<number, OverlayBox[]>();
    for (const source of sources) {
      if (source.page_number === null || source.bbox === null) {
        continue;
      }
      const list = map.get(source.page_number) ?? [];
      list.push({ source, active: source.chunk_id === activeChunkId });
      map.set(source.page_number, list);
    }
    return map;
  }, [sources, activeChunkId]);

  useEffect(() => {
    if (!activeChunkId) {
      return;
    }
    const target = sources.find((source) => source.chunk_id === activeChunkId);
    if (!target?.page_number) {
      return;
    }
    const element = pageRefs.current.get(target.page_number);
    element?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeChunkId, sources]);

  const handleScroll = useCallback(() => {
    const container = scrollRef.current;
    if (!container) {
      return;
    }
    const midpoint = container.scrollTop + container.clientHeight / 2;

    let current = 1;
    for (const [pageNumber, element] of pageRefs.current) {
      if (element.offsetTop <= midpoint) {
        current = Math.max(current, pageNumber);
      }
    }
    setVisiblePage(current);
  }, []);

  const zoom = useCallback((delta: number) => {
    setScale((previous) =>
      Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number((previous + delta).toFixed(2)))),
    );
  }, []);

  if (loading) {
    return (
      <div
        className={`flex h-full items-center justify-center ${className}`}
        role="status"
      >
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Opening document…
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className={`flex h-full flex-col items-center justify-center gap-2 p-6 text-center ${className}`}
        role="alert"
      >
        <AlertTriangle className="h-5 w-5 text-destructive" aria-hidden="true" />
        <p className="text-sm font-medium">Can&apos;t open this document</p>
        <p className="max-w-sm text-xs text-muted-foreground">{error}</p>
      </div>
    );
  }

  if (!doc) {
    return null;
  }

  return (
    <div className={`flex h-full flex-col ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          Page {visiblePage} of {doc.numPages}
        </p>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => zoom(-SCALE_STEP)}
            disabled={scale <= MIN_SCALE}
            aria-label="Zoom out"
            className="rounded-md border border-border p-1 hover:bg-muted disabled:opacity-40"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <span className="w-12 text-center text-xs tabular-nums text-muted-foreground">
            {Math.round(scale * 100)}%
          </span>
          <button
            type="button"
            onClick={() => zoom(SCALE_STEP)}
            disabled={scale >= MAX_SCALE}
            aria-label="Zoom in"
            className="rounded-md border border-border p-1 hover:bg-muted disabled:opacity-40"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="min-h-0 flex-1 overflow-auto bg-muted/30 p-4"
      >
        <div className="mx-auto flex w-fit flex-col gap-4">
          {geometry.map((page, index) => {
            const pageNumber = index + 1;
            const withinWindow =
              Math.abs(pageNumber - visiblePage) <= WINDOW_RADIUS;

            return (
              <div
                key={pageNumber}
                ref={(element) => {
                  if (element) {
                    pageRefs.current.set(pageNumber, element);
                  } else {
                    pageRefs.current.delete(pageNumber);
                  }
                }}
                className="relative bg-white shadow-sm"
                style={{
                  width: page.widthPt * scale,
                  height: page.heightPt * scale,
                }}
              >
                {withinWindow ? (
                  <>
                    <PdfPage doc={doc} pageNumber={pageNumber} scale={scale} />
                    <BboxOverlay
                      boxes={sourcesByPage.get(pageNumber) ?? []}
                      pageWidthPt={page.widthPt}
                      pageHeightPt={page.heightPt}
                      {...(onBoxClick ? { onBoxClick } : {})}
                    />
                  </>
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                    Page {pageNumber}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

interface PdfPageProps {
  readonly doc: PDFDocumentProxy;
  readonly pageNumber: number;
  readonly scale: number;
}

const PdfPage: React.FC<PdfPageProps> = ({ doc, pageNumber, scale }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;
    let page: PDFPageProxy | null = null;
    let task: ReturnType<PDFPageProxy["render"]> | null = null;

    const render = async () => {
      const canvas = canvasRef.current;
      if (!canvas) {
        return;
      }

      page = await doc.getPage(pageNumber);
      if (cancelled) {
        page.cleanup();
        return;
      }

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const viewport = page.getViewport({ scale: scale * dpr });

      const context = canvas.getContext("2d");
      if (!context) {
        return;
      }

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width / dpr}px`;
      canvas.style.height = `${viewport.height / dpr}px`;

      task = page.render({ canvas, canvasContext: context, viewport });

      try {
        await task.promise;
      } catch {
        // Cancelled render throws
      }
    };

    void render();

    return () => {
      cancelled = true;
      task?.cancel();
      page?.cleanup();
    };
  }, [doc, pageNumber, scale]);

  return <canvas ref={canvasRef} className="block" />;
};

export default PdfViewer;
