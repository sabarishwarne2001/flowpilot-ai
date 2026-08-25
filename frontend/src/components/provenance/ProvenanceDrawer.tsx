import React, { useCallback, useEffect, useMemo, useRef } from "react";
import { FileText, ShieldAlert, ShieldCheck, X } from "lucide-react";

import type {
  CitationClaim,
  CitationEnvelope,
  CitationSource,
} from "@/services/streaming/resumableStream";

const CONTEXT_HASH_PATTERN = /^sha256:[0-9a-f]{64}$/;

export interface ProvenanceDrawerProps {
  readonly open: boolean;
  readonly envelope: CitationEnvelope | null;
  readonly activeClaimId?: string | null;
  readonly onClose: () => void;
  readonly onOpenSource?: (source: CitationSource) => void;
}

export const ProvenanceDrawer: React.FC<ProvenanceDrawerProps> = ({
  open,
  envelope,
  activeClaimId = null,
  onClose,
  onOpenSource,
}) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLLIElement>(null);

  const sealState = useMemo(() => {
    if (!envelope) {
      return "absent" as const;
    }
    const hasBoth =
      Boolean(envelope.context_hash) && Boolean(envelope.audit_log_id);
    if (!hasBoth) {
      return "unsealed" as const;
    }
    return CONTEXT_HASH_PATTERN.test(envelope.context_hash ?? "")
      ? ("sealed" as const)
      : ("malformed" as const);
  }, [envelope]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (open && activeClaimId && activeRef.current) {
      activeRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [open, activeClaimId, envelope]);

  const copyAuditId = useCallback(async () => {
    if (!envelope?.audit_log_id) {
      return;
    }
    try {
      await navigator.clipboard.writeText(envelope.audit_log_id);
    } catch {
      // Ignore denied clipboard
    }
  }, [envelope]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="provenance-title"
        className="flex h-full w-full max-w-md flex-col border-l border-border bg-card shadow-2xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-border p-4">
          <div className="min-w-0">
            <h2 id="provenance-title" className="text-base font-semibold">
              Where this came from
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {envelope
                ? `${envelope.claims.length} cited ${envelope.claims.length === 1 ? "claim" : "claims"}`
                : "No provenance recorded"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {envelope && (
          <div className="border-b border-border p-4">
            {sealState === "sealed" && (
              <div className="flex items-start gap-2">
                <ShieldCheck
                  className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600"
                  aria-hidden="true"
                />
                <div className="min-w-0 text-xs">
                  <p className="font-medium text-foreground">
                    Sealed to an audit record
                  </p>
                  <p className="mt-0.5 text-muted-foreground">
                    The exact context sent to the model was hashed and recorded
                    when this answer was generated. Check the hash against the
                    audit entry to confirm it hasn&apos;t changed.
                  </p>
                </div>
              </div>
            )}

            {sealState === "unsealed" && (
              <div className="flex items-start gap-2">
                <FileText
                  className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
                <div className="text-xs">
                  <p className="font-medium text-foreground">
                    Answered without documents
                  </p>
                  <p className="mt-0.5 text-muted-foreground">
                    Nothing was retrieved for this question, so there is no
                    context to seal.
                  </p>
                </div>
              </div>
            )}

            {sealState === "malformed" && (
              <div className="flex items-start gap-2">
                <ShieldAlert
                  className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
                  aria-hidden="true"
                />
                <div className="text-xs">
                  <p className="font-medium text-destructive">
                    Seal is not in the expected format
                  </p>
                  <p className="mt-0.5 text-muted-foreground">
                    Report this with the audit id below.
                  </p>
                </div>
              </div>
            )}

            {envelope.context_hash && (
              <dl className="mt-3 space-y-2">
                <div>
                  <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    Context hash
                  </dt>
                  <dd className="mt-0.5 break-all font-mono text-[11px] leading-relaxed">
                    {envelope.context_hash}
                  </dd>
                </div>

                {envelope.audit_log_id && (
                  <div>
                    <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                      Audit entry
                    </dt>
                    <dd className="mt-0.5 flex items-center gap-2">
                      <span className="break-all font-mono text-[11px]">
                        {envelope.audit_log_id}
                      </span>
                      <button
                        type="button"
                        onClick={() => void copyAuditId()}
                        className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] hover:bg-muted"
                      >
                        Copy
                      </button>
                    </dd>
                  </div>
                )}
              </dl>
            )}

            <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
              Verification happens against the audit log, not in this panel —
              the original context isn&apos;t sent to your browser.
            </p>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {!envelope || envelope.claims.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">
              This answer didn&apos;t cite any passages.
            </p>
          ) : (
            <ol className="divide-y divide-border">
              {envelope.claims.map((claim, index) => (
                <ClaimEntry
                  key={claim.claim_id}
                  ref={claim.claim_id === activeClaimId ? activeRef : null}
                  claim={claim}
                  index={index + 1}
                  active={claim.claim_id === activeClaimId}
                  {...(onOpenSource ? { onOpenSource } : {})}
                />
              ))}
            </ol>
          )}
        </div>

        {envelope && (
          <footer className="border-t border-border px-4 py-3 text-[11px] text-muted-foreground">
            {envelope.provider && envelope.model && (
              <p>
                {envelope.provider} · {envelope.model}
                {envelope.prompt_version && ` · prompt v${envelope.prompt_version}`}
              </p>
            )}
            {(envelope.passages_dropped_budget > 0 ||
              envelope.passages_dropped_injection > 0) && (
              <p className="mt-1">
                {envelope.passages_included} passages used
                {envelope.passages_dropped_budget > 0 &&
                  `, ${envelope.passages_dropped_budget} dropped for space`}
                {envelope.passages_dropped_injection > 0 &&
                  `, ${envelope.passages_dropped_injection} removed by the safety filter`}
                .
              </p>
            )}
          </footer>
        )}
      </div>
    </div>
  );
};

interface ClaimEntryProps {
  readonly claim: CitationClaim;
  readonly index: number;
  readonly active: boolean;
  readonly onOpenSource?: (source: CitationSource) => void;
}

const ClaimEntry = React.forwardRef<HTMLLIElement, ClaimEntryProps>(
  ({ claim, index, active, onOpenSource }, ref) => (
    <li
      ref={ref}
      className={`p-4 ${active ? "bg-primary/5" : ""}`}
      aria-current={active ? "true" : undefined}
    >
      <p className="text-xs font-medium text-muted-foreground">
        Claim {index}
      </p>

      <ul className="mt-2 space-y-2">
        {claim.sources.map((source) => {
          const locatable = source.bbox !== null && source.page_number !== null;

          return (
            <li
              key={source.chunk_id}
              className="rounded-md border border-border bg-background p-2.5"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 truncate text-xs font-medium">
                  {source.original_filename}
                </p>
                {source.page_number !== null && (
                  <span className="shrink-0 text-[11px] text-muted-foreground">
                    p.{source.page_number}
                  </span>
                )}
              </div>

              <p className="mt-1.5 line-clamp-4 text-xs leading-relaxed text-muted-foreground">
                {source.snippet}
              </p>

              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="text-[10px] text-muted-foreground">
                  {Math.round(source.similarity_score * 100)}% match
                </span>

                {onOpenSource && (
                  <button
                    type="button"
                    onClick={() => onOpenSource(source)}
                    className="rounded border border-border px-2 py-0.5 text-[11px] hover:bg-muted"
                  >
                    {locatable ? "Show in document" : "Open document"}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </li>
  ),
);

ClaimEntry.displayName = "ClaimEntry";

export default ProvenanceDrawer;
