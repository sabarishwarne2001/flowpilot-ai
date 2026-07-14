import React, { useEffect, useRef, useCallback } from "react";

import { X, FileText, Bookmark, Percent, FileCheck } from "lucide-react";

import type { SourceCitation } from "@/types/assistant";

interface CitationDrawerProps {
  /**
   * Controls whether the drawer is visible.
   */
  readonly isOpen: boolean;

  /**
   * Invoked when the drawer should close.
   */
  readonly onClose: () => void;

  /**
   * Citation currently being displayed.
   */
  readonly citation: SourceCitation | null;

  readonly className?: string;
}

/* ============================================================================
   Constants
============================================================================ */

const DRAWER_TITLE_ID = "citation-drawer-title";

const DRAWER_DESCRIPTION_ID = "citation-drawer-description";

/* ============================================================================
   Helper Functions
============================================================================ */

/**
 * Formats similarity score as a percentage.
 */
const formatSimilarityScore = (score: number): string =>
  `${(score * 100).toFixed(1)}%`;

/* ============================================================================
   Component
============================================================================ */

export const CitationDrawer: React.FC<CitationDrawerProps> = ({
  isOpen,
  onClose,
  citation,
  className = "",
}) => {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  const previousFocusedElement = useRef<HTMLElement | null>(null);

  /**
   * --------------------------------------------------------------------------
   * Save previously focused element
   * --------------------------------------------------------------------------
   */

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    previousFocusedElement.current =
      document.activeElement as HTMLElement | null;
  }, [isOpen]);

  /**
   * --------------------------------------------------------------------------
   * Focus close button after opening
   * --------------------------------------------------------------------------
   */

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    closeButtonRef.current?.focus();
  }, [isOpen]);

  /**
   * --------------------------------------------------------------------------
   * Restore previous focus after closing
   * --------------------------------------------------------------------------
   */

  useEffect(() => {
    if (isOpen) {
      return;
    }

    previousFocusedElement.current?.focus();
  }, [isOpen]);

  /**
   * --------------------------------------------------------------------------
   * Prevent background scrolling
   * --------------------------------------------------------------------------
   */

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const previousOverflow = document.body.style.overflow;

    document.body.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  /**
   * --------------------------------------------------------------------------
   * Escape key support
   * --------------------------------------------------------------------------
   */

  const handleEscapeKey = useCallback(
    (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
      }
    },
    [onClose]
  );

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    window.addEventListener("keydown", handleEscapeKey);

    return () => {
      window.removeEventListener("keydown", handleEscapeKey);
    };
  }, [isOpen, handleEscapeKey]);

  /**
   * --------------------------------------------------------------------------
   * Nothing selected
   * --------------------------------------------------------------------------
   */

  if (!isOpen || !citation) {
    return null;
  }

  const similarityScore = formatSimilarityScore(citation.similarity_score);

  const documentName =
    citation.document_display_name ?? citation.original_filename;

  return (
    <div
      className={`fixed inset-0 z-50 flex justify-end ${className}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby={DRAWER_TITLE_ID}
      aria-describedby={DRAWER_DESCRIPTION_ID}
    >
      {/* ======================================================
          Overlay
      ====================================================== */}

      <button
        type="button"
        onClick={onClose}
        aria-label="Close citation drawer"
        className="
          fixed
          inset-0
          cursor-default
          bg-black/40
          backdrop-blur-sm
          animate-in
          fade-in
        "
      />

      {/* ======================================================
          Drawer
      ====================================================== */}

      <aside
        className="
          relative
          flex
          h-screen
          w-full
          max-w-md
          flex-col
          border-l
          border-border/80
          bg-card
          shadow-2xl
          animate-in
          slide-in-from-right
        "
      >
        {/* ======================================================
            Header
        ====================================================== */}

        <header className="flex h-16 items-center justify-between border-b border-border/40 bg-muted/5 px-6">
          <div className="flex items-center gap-2.5">
            <FileCheck className="h-5 w-5 text-primary" />

            <div>
              <h2
                id={DRAWER_TITLE_ID}
                className="text-sm font-extrabold uppercase tracking-wider"
              >
                Cited Source
              </h2>

              <p
                id={DRAWER_DESCRIPTION_ID}
                className="text-xs text-muted-foreground"
              >
                Review the document fragment used to generate this AI response.
              </p>
            </div>
          </div>

          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="
              rounded-lg
              p-2
              text-muted-foreground
              transition-colors
              hover:bg-muted/50
              hover:text-foreground
              focus:outline-none
              focus:ring-2
              focus:ring-primary/20
            "
            aria-label="Close citation drawer"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        {/* ======================================================
            Scrollable Content
        ====================================================== */}

        <main className="flex flex-1 flex-col overflow-y-auto p-6">
          {/* ======================================================
              Source Document
          ====================================================== */}

          <section className="space-y-2">
            <h3 className="text-[11px] font-black uppercase tracking-widest text-muted-foreground">
              Source Document
            </h3>

            <div className="flex items-center gap-3.5 rounded-xl border border-border/40 bg-muted/20 p-3.5">
              <div className="rounded-lg bg-primary/10 p-2.5 text-primary">
                <FileText className="h-5 w-5 flex-shrink-0" />
              </div>

              <div className="min-w-0">
                <p
                  className="truncate pr-4 text-sm font-extrabold text-foreground/90"
                  title={documentName}
                >
                  {documentName}
                </p>

                <p className="mt-1 text-[10px] font-bold text-muted-foreground">
                  {citation.page_number != null
                    ? `Page ${citation.page_number} • Chunk ${citation.chunk_index}`
                    : `Chunk ${citation.chunk_index}`}
                </p>
              </div>
            </div>
          </section>

          {/* ======================================================
              Metrics
          ====================================================== */}

          <section className="mt-6 grid grid-cols-2 gap-4">
            <div className="space-y-1.5 rounded-xl border border-border/40 bg-muted/10 p-4">
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Percent className="h-4 w-4" />

                <span className="text-[10px] font-bold uppercase tracking-wider">
                  Relevance
                </span>
              </div>

              <p className="text-xl font-black tracking-tight">
                {similarityScore}
              </p>
            </div>

            <div className="space-y-1.5 rounded-xl border border-border/40 bg-muted/10 p-4">
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Bookmark className="h-4 w-4" />

                <span className="text-[10px] font-bold uppercase tracking-wider">
                  Location
                </span>
              </div>

              <p className="text-xl font-black tracking-tight">
                {citation.page_number != null
                  ? `Page ${citation.page_number}`
                  : "Text Segment"}
              </p>
            </div>
          </section>
          {/* ======================================================
              Citation Snippet
          ====================================================== */}

          <section className="mt-6 flex min-h-0 flex-1 flex-col space-y-2">
            <h3 className="text-[11px] font-black uppercase tracking-widest text-muted-foreground">
              Referenced Text
            </h3>

            <div
              className="
                flex-1
                overflow-y-auto
                rounded-xl
                border
                border-border/40
                bg-muted/5
                p-4
              "
            >
              <p className="whitespace-pre-wrap break-words text-sm leading-7 text-foreground/90">
                {citation.snippet}
              </p>
            </div>
          </section>

          {/* ======================================================
              Metadata
          ====================================================== */}

          <section className="mt-6 space-y-2">
            <h3 className="text-[11px] font-black uppercase tracking-widest text-muted-foreground">
              Metadata
            </h3>

            <div className="rounded-xl border border-border/40 bg-muted/10 p-4">
              <dl className="space-y-4">
                <div className="flex items-start justify-between gap-4">
                  <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                    Citation ID
                  </dt>

                  <dd className="break-all text-right font-mono text-xs">
                    {citation.citation_id}
                  </dd>
                </div>

                <div className="flex items-start justify-between gap-4">
                  <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                    Work Item
                  </dt>

                  <dd className="break-all text-right font-mono text-xs">
                    {citation.work_item_id}
                  </dd>
                </div>

                <div className="flex items-start justify-between gap-4">
                  <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                    Chunk Index
                  </dt>

                  <dd className="text-sm font-semibold">
                    {citation.chunk_index}
                  </dd>
                </div>
              </dl>
            </div>
          </section>
        </main>
        {/* ======================================================
            Footer
        ====================================================== */}

        <footer className="border-t border-border/40 bg-muted/5 p-4">
          <p className="text-center text-[11px] font-medium text-muted-foreground">
            Source citation generated from the document retrieval pipeline.
            Similarity scores are intended to help explain why this document
            fragment was selected by the RAG engine.
          </p>
        </footer>
      </aside>
    </div>
  );
};
export default CitationDrawer;
