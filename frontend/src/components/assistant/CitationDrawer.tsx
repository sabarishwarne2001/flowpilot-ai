import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Check, ChevronLeft, ChevronRight, Copy, ExternalLink, FileText, Quote, X } from "lucide-react";

import { workItemDetailsPath } from "@/routes/tenantPaths";
import { citationKey, type SourceCitation } from "@/types/assistant";

interface CitationDrawerProps {
  /** Controls whether the drawer is visible. */
  readonly isOpen: boolean;
  /** Invoked when the drawer should close. */
  readonly onClose: () => void;
  /** Citation currently being displayed. */
  readonly citation: SourceCitation | null;
  /** Every source of the answer, in the order the answer numbers them; enables previous / next. */
  readonly citations?: readonly SourceCitation[];
  /** Show another source of the same answer. */
  readonly onSelect?: (citation: SourceCitation) => void;
  readonly className?: string;
}

const DRAWER_TITLE_ID = "citation-drawer-title";
const DRAWER_DESCRIPTION_ID = "citation-drawer-description";

const relevance = (score: number): string => `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`;

/**
 * The passage an assistant answer cited.
 *
 * Phase 2 (F-175 and the layout defect reported live):
 *  - Sources carry no `citation_id` (the API never sent one): every source shared the key
 *    `undefined`, React warned on each answer, and the drawer printed an empty "Citation ID".
 *    Keys come from `citationKey`; the drawer shows what a person can use instead.
 *  - The referenced text sat in a nested scroll box sized by what was left of the viewport, so on
 *    a short screen it collapsed to a sliver, and a flex row could squeeze its column until the text
 *    broke letter by letter. The passage is now the drawer's single scroll region, in a column that
 *    keeps its full width (`min-w-0` on every flex ancestor, `overflow-wrap: anywhere`).
 *  - Previous / next walk the answer's sources; "Open document" and "Copy passage" act on it.
 */
export const CitationDrawer: React.FC<CitationDrawerProps> = ({
  isOpen,
  onClose,
  citation,
  citations = [],
  onSelect,
  className = "",
}) => {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousFocusedElement = useRef<HTMLElement | null>(null);
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const [copied, setCopied] = useState(false);

  const index = useMemo(() => {
    if (!citation) {
      return -1;
    }
    const key = citationKey(citation);
    return citations.findIndex((item) => citationKey(item) === key);
  }, [citation, citations]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }
    previousFocusedElement.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      previousFocusedElement.current?.focus();
    };
  }, [isOpen]);

  useEffect(() => setCopied(false), [citation]);

  const go = useCallback(
    (step: number) => {
      const next = citations[index + step];
      if (next && onSelect) {
        onSelect(next);
      }
    },
    [citations, index, onSelect],
  );

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowLeft" || event.key === "[") {
        go(-1);
      } else if (event.key === "ArrowRight" || event.key === "]") {
        go(1);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose, go]);

  const copy = useCallback(async () => {
    if (!citation) {
      return;
    }
    try {
      await navigator.clipboard.writeText(citation.snippet);
      setCopied(true);
    } catch {
      // A denied clipboard leaves the passage selectable on screen.
    }
  }, [citation]);

  if (!isOpen || !citation) {
    return null;
  }

  const documentName = citation.document_display_name ?? citation.original_filename;
  const position = index >= 0 && citations.length > 1 ? `Source ${index + 1} of ${citations.length}` : "Cited source";
  const documentHref =
    orgSlug && workspaceSlug ? workItemDetailsPath(orgSlug, workspaceSlug, citation.work_item_id) : null;

  return (
    <div
      className={`fixed inset-0 z-50 flex justify-end ${className}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby={DRAWER_TITLE_ID}
      aria-describedby={DRAWER_DESCRIPTION_ID}
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="Close citation drawer"
        tabIndex={-1}
        className="fixed inset-0 cursor-default bg-black/40 backdrop-blur-[2px]"
      />

      <aside
        className="relative flex h-full w-full min-w-0 flex-col border-l border-border/80 bg-card shadow-2xl sm:w-[30rem] sm:max-w-[100vw]"
        data-testid="citation-drawer"
      >
        <header className="shrink-0 border-b border-border/60 px-5 pb-4 pt-4">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{position}</p>
            <div className="flex shrink-0 items-center gap-1">
              {citations.length > 1 && onSelect ? (
                <>
                  <button
                    type="button"
                    onClick={() => go(-1)}
                    disabled={index <= 0}
                    aria-label="Previous source"
                    className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => go(1)}
                    disabled={index < 0 || index >= citations.length - 1}
                    aria-label="Next source"
                    className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </>
              ) : null}
              <button
                ref={closeButtonRef}
                type="button"
                onClick={onClose}
                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                aria-label="Close citation drawer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="mt-2 flex min-w-0 items-start gap-3">
            <span className="mt-0.5 shrink-0 rounded-lg bg-primary/10 p-2 text-primary">
              <FileText className="h-4 w-4" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <h2
                id={DRAWER_TITLE_ID}
                className="text-[15px] font-semibold leading-snug text-foreground [overflow-wrap:anywhere]"
              >
                {documentName}
              </h2>
              <p id={DRAWER_DESCRIPTION_ID} className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-foreground/80">
                  {citation.page_number !== null ? `Page ${citation.page_number}` : "No page number"}
                </span>
                <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-foreground/80">
                  Passage {citation.chunk_index + 1}
                </span>
                <span
                  className="rounded-full bg-primary/10 px-2 py-0.5 font-medium text-primary"
                  title="How closely this passage matched the question"
                >
                  {relevance(citation.similarity_score)} match
                </span>
              </p>
            </div>
          </div>
        </header>

        {/* The one scroll region: the passage keeps the drawer's full width at every size. */}
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5">
          <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            <Quote className="h-3.5 w-3.5" aria-hidden />
            Referenced text
          </h3>
          <blockquote
            className="w-full min-w-0 rounded-lg border-l-[3px] border-primary/60 bg-muted/30 px-4 py-3"
            data-testid="citation-passage"
          >
            <p className="whitespace-pre-wrap text-[13.5px] leading-6 text-foreground/90 [overflow-wrap:anywhere]">
              {citation.snippet}
            </p>
          </blockquote>

          <details className="mt-5 rounded-lg border border-border/60 px-4 py-2.5 text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none font-medium text-foreground/80">Technical details</summary>
            <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5">
              <dt>Document id</dt>
              <dd className="min-w-0 font-mono [overflow-wrap:anywhere]">{citation.work_item_id}</dd>
              <dt>Passage index</dt>
              <dd className="font-mono">{citation.chunk_index}</dd>
              <dt>Similarity</dt>
              <dd className="font-mono">{citation.similarity_score.toFixed(4)}</dd>
            </dl>
          </details>
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-border/60 px-5 py-3">
          <button
            type="button"
            onClick={() => void copy()}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-foreground/80 transition-colors hover:bg-muted"
          >
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "Copied" : "Copy passage"}
          </button>
          {documentHref ? (
            <Link
              to={documentHref}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90"
            >
              Open document
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : null}
        </footer>
      </aside>
    </div>
  );
};

export default CitationDrawer;
