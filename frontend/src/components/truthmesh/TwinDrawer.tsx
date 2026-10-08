/**
 * Phase 2 — one document's twin, in a side drawer: what it is, what it states (amounts, dates,
 * parties, terms with their sentences), what it is linked to and why (each signal named), the
 * conflicts it is part of, and "Simulate from here". A contributor confirms or rejects a link; a
 * rejected link stops carrying conflicts at once and is never re-created by a rebuild.
 */
import React, { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Check, ExternalLink, Loader2, Sparkles, Undo2, X } from "lucide-react";
import { toast } from "sonner";

import { ConflictCard } from "@/components/truthmesh/ConflictCard";
import { KindChip, money, RiskBadge, SIGNAL_LABELS, StrengthBar } from "@/components/truthmesh/shared";
import { workItemDetailsPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { decideLink, getMeshDocument, meshKeys } from "@/services/api/truthmesh";
import type { MeshLink } from "@/types/truthmesh";

interface TwinDrawerProps {
  readonly workspaceId: string;
  readonly workItemId: string;
  readonly canDecide: boolean;
  readonly onClose: () => void;
  readonly onNavigate: (workItemId: string) => void;
  readonly onSimulate: (workItemId: string) => void;
}

const TERM_LABELS: Readonly<Record<string, string>> = {
  payment_days: "Payment",
  notice_days: "Notice",
  delivery_days: "Delivery",
  termination_deadline: "Notice deadline",
  liquidated_damages_pct: "Liquidated damages",
  late_interest_pct: "Late interest",
  liability_cap_months: "Liability cap",
  governing_law: "Governing law",
  effective_date: "Effective",
  end_date: "Ends",
};

const termValue = (value: string | number, unit: string): string => {
  if (unit === "days") {
    return `${value} days`;
  }
  if (unit === "months") {
    return `${value} months of fees`;
  }
  if (unit.startsWith("%/")) {
    return `${value}% per ${unit.slice(2)}`;
  }
  return String(value);
};

const OtherButton: React.FC<{ readonly link: MeshLink; readonly onNavigate: (id: string) => void }> = ({ link, onNavigate }) =>
  link.other ? (
    <button type="button" className="font-semibold text-primary hover:underline [overflow-wrap:anywhere]" onClick={() => onNavigate(link.other!.work_item_id)}>
      {link.other.title}
    </button>
  ) : null;

export const TwinDrawer: React.FC<TwinDrawerProps> = ({ workspaceId, workItemId, canDecide, onClose, onNavigate, onSimulate }) => {
  const queryClient = useQueryClient();
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const query = useQuery({
    queryKey: meshKeys.document(workspaceId, workItemId),
    queryFn: () => getMeshDocument(workspaceId, workItemId),
    staleTime: 10_000,
  });
  const link = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "CONFIRMED" | "REJECTED" | "AUTO" }) =>
      decideLink(workspaceId, id, decision),
    onSuccess: async (updated) => {
      toast.success(updated.status === "REJECTED" ? "Link rejected: its conflicts were recomputed" : updated.status === "CONFIRMED" ? "Link confirmed" : "Link back to automatic");
      await queryClient.invalidateQueries({ queryKey: meshKeys.all(workspaceId) });
    },
    onError: (error) => toast.error(errorMessage(error, "The link decision was not saved.")),
  });

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const doc = query.data;
  const node = doc?.node;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Document twin">
      <button type="button" tabIndex={-1} aria-label="Close" onClick={onClose} className="fixed inset-0 cursor-default bg-black/35 backdrop-blur-[2px]" />
      <aside className="relative flex h-full w-full min-w-0 flex-col border-l border-border bg-card shadow-2xl sm:w-[34rem]" data-testid="twin-drawer">
        <header className="shrink-0 border-b border-border px-5 py-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Document twin</span>
            <button ref={closeRef} type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </div>
          {node ? (
            <div className="mt-2 min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <KindChip kind={node.kind} label={node.kind_label} />
                <RiskBadge score={node.risk_score} />
              </div>
              <h2 className="mt-1.5 text-lg font-semibold tracking-tight [overflow-wrap:anywhere]">{node.title}</h2>
              <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">{node.filename}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" className="fp-btn fp-btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-xs" onClick={() => onSimulate(node.work_item_id)}>
                  <Sparkles className="h-3.5 w-3.5" aria-hidden />
                  Simulate from here
                </button>
                {orgSlug && workspaceSlug ? (
                  <Link to={workItemDetailsPath(orgSlug, workspaceSlug, node.work_item_id)} className="fp-btn fp-btn-secondary inline-flex items-center gap-1.5 px-3 py-1.5 text-xs">
                    Open document
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  </Link>
                ) : null}
              </div>
            </div>
          ) : null}
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {query.isLoading ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Loading the twin…
            </p>
          ) : query.isError || !doc || !node ? (
            <p className="text-sm text-destructive">{errorMessage(query.error, "This document's twin could not be loaded.")}</p>
          ) : (
            <>
              <section aria-label="Key facts">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm">
                  {[
                    ["Counterparty", node.counterparty],
                    ["Amount", node.amount_micros !== null ? money(node.amount_micros, node.currency) : null],
                    ["Net of tax", node.net_amount_micros !== null && node.net_amount_micros !== node.amount_micros ? money(node.net_amount_micros, node.currency) : null],
                    ["Dated", node.document_date],
                    ["Term", node.effective_date || node.end_date ? `${node.effective_date ?? "…"} → ${node.end_date ?? "open"}` : null],
                    ["Parties", doc.parties.length ? doc.parties.join(", ") : null],
                  ]
                    .filter(([, value]) => value)
                    .map(([label, value]) => (
                      <div key={label} className="min-w-0">
                        <dt className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">{label}</dt>
                        <dd className="mt-0.5 font-medium [overflow-wrap:anywhere]">{value}</dd>
                      </div>
                    ))}
                </dl>
              </section>

              {Object.keys(doc.terms).length > 0 ? (
                <section aria-label="Terms">
                  <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Terms it states</h3>
                  <ul className="space-y-2">
                    {Object.entries(doc.terms).map(([key, term]) => (
                      <li key={key} className="rounded-lg border border-border/70 px-3 py-2">
                        <div className="flex items-center justify-between gap-2 text-sm">
                          <span className="text-muted-foreground">{TERM_LABELS[key] ?? key.replace(/_/g, " ")}</span>
                          <span className="font-semibold">{termValue(term.value, term.unit)}</span>
                        </div>
                        {term.source === "text" ? (
                          <p className="mt-1 text-[11.5px] italic leading-snug text-muted-foreground [overflow-wrap:anywhere]">“{term.quote}”</p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              <section aria-label="Links">
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  Linked documents ({doc.links.length})
                </h3>
                {doc.links.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Nothing links to this document yet.</p>
                ) : (
                  <ul className="space-y-2">
                    {doc.links.map((l: MeshLink) => (
                      <li key={l.id} className={`rounded-lg border px-3 py-2.5 ${l.status === "REJECTED" ? "border-dashed border-border opacity-60" : "border-border/70"}`} data-testid="twin-link">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0 text-sm">
                            {l.outgoing || !l.directed ? (
                              <>
                                <span className="text-muted-foreground">This {l.relation_label} </span>
                                <OtherButton link={l} onNavigate={onNavigate} />
                              </>
                            ) : (
                              <>
                                <OtherButton link={l} onNavigate={onNavigate} />
                                <span className="text-muted-foreground"> {l.relation_label} this</span>
                              </>
                            )}
                            {l.other ? <span className="ml-1 text-xs text-muted-foreground">({l.other.kind_label.toLowerCase()})</span> : null}
                          </div>
                          <StrengthBar value={l.strength} />
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1">
                          {l.signals.map((s, i) => (
                            <span key={`${s.kind}-${i}`} className="rounded bg-muted px-1.5 py-0.5 text-[10.5px] text-foreground/75" title={`weight ${Math.round(s.weight * 100)}%`}>
                              {SIGNAL_LABELS[s.kind] ?? s.kind}: {String(s.value)}
                            </span>
                          ))}
                          {l.status !== "AUTO" ? (
                            <span className="rounded border border-border px-1.5 py-0.5 text-[10.5px] font-semibold text-muted-foreground">{l.status.toLowerCase()} by a person</span>
                          ) : null}
                        </div>
                        {canDecide ? (
                          <div className="mt-2 flex gap-1.5">
                            {l.status !== "CONFIRMED" ? (
                              <button type="button" className="fp-btn fp-btn-ghost inline-flex items-center gap-1 px-2 py-0.5 text-[11px]" disabled={link.isPending} onClick={() => link.mutate({ id: l.id, decision: "CONFIRMED" })}>
                                <Check className="h-3 w-3" aria-hidden />
                                Confirm
                              </button>
                            ) : null}
                            {l.status !== "REJECTED" ? (
                              <button type="button" className="fp-btn fp-btn-ghost inline-flex items-center gap-1 px-2 py-0.5 text-[11px]" disabled={link.isPending} onClick={() => link.mutate({ id: l.id, decision: "REJECTED" })}>
                                <X className="h-3 w-3" aria-hidden />
                                Not linked
                              </button>
                            ) : null}
                            {l.status !== "AUTO" ? (
                              <button type="button" className="fp-btn fp-btn-ghost inline-flex items-center gap-1 px-2 py-0.5 text-[11px]" disabled={link.isPending} onClick={() => link.mutate({ id: l.id, decision: "AUTO" })}>
                                <Undo2 className="h-3 w-3" aria-hidden />
                                Undo
                              </button>
                            ) : null}
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section aria-label="Conflicts">
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  Conflicts ({doc.conflicts.filter((c) => c.status === "OPEN" || c.status === "ACKNOWLEDGED").length} open)
                </h3>
                {doc.conflicts.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No conflict involves this document.</p>
                ) : (
                  <div className="space-y-2">
                    {doc.conflicts.map((c) => (
                      <ConflictCard key={c.id} workspaceId={workspaceId} conflict={c} canDecide={canDecide} onOpenDocument={onNavigate} />
                    ))}
                  </div>
                )}
              </section>

              {doc.facts.length > 0 ? (
                <details className="rounded-lg border border-border/70 px-3 py-2">
                  <summary className="cursor-pointer text-sm font-medium">Everything it states ({doc.facts.length})</summary>
                  <dl className="mt-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-x-3 gap-y-1 text-xs">
                    {doc.facts.map((f) => (
                      <React.Fragment key={f.concept}>
                        <dt className="truncate text-muted-foreground" title={f.label}>{f.label}</dt>
                        <dd className="font-mono [overflow-wrap:anywhere]">{f.display}</dd>
                      </React.Fragment>
                    ))}
                  </dl>
                </details>
              ) : null}
            </>
          )}
        </div>
      </aside>
    </div>
  );
};

export default TwinDrawer;
