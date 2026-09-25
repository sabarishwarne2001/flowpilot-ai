/**
 * ARCH42-S2:page-list — the workspace's entity graph: every canonical person,
 * organization, address, account, asset and shipment, with how many documents
 * name it. Search takes a name, or an identifier (email, PAN, GSTIN, IBAN,
 * passport, Aadhaar, phone) that the server matches by HMAC without ever
 * storing it.
 */
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2, Lock, Network, Search } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { ErrorState } from "@/components/common/ErrorState";
import { HINT, INPUT, PAGE_TITLE, SCROLL_X, SECTION_TITLE, SURFACE, SURFACE_INSET, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { entityPath, verificationPath } from "@/routes/tenantPaths";
import { entityKeys, getEntityPotential, getEntitySummary, listEntities } from "@/services/api/entities";
import { errorMessage } from "@/services/api/errors";
import { ENTITY_KIND_LABELS, ENTITY_KINDS, type EntityKind } from "@/types/entities";
// ARCH46-S2:h8-timestamps — instants follow the reader's profile zone and language (ARCH-30 D-5; verify_arch30_tranche3 H8).
import { formatTimestampDate } from "@/utils/displayTime";

const PAGE_SIZE = 25;

const LockedView: React.FC<{ readonly workspaceId: string }> = ({ workspaceId }) => {
  const potential = useQuery({
    queryKey: entityKeys.potential(workspaceId),
    queryFn: () => getEntityPotential(workspaceId),
    enabled: Boolean(workspaceId),
  });
  return (
    <section className={`${SURFACE} mx-auto max-w-2xl space-y-3 p-6`} aria-labelledby="entities-lock">
      <div className="flex items-center gap-2">
        <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h2 id="entities-lock" className={SECTION_TITLE}>
          Entity graph
        </h2>
      </div>
      <p className="text-sm text-muted-foreground">
        One record per vendor, customer, patient, employee, account, container and shipment across every document, with the
        relationships between them — so &ldquo;show me everything tied to this vendor&rdquo; is one click.
      </p>
      {potential.data && potential.data.documents > 0 ? (
        <p className="text-sm">
          {potential.data.documents.toLocaleString()} of your documents already name people or organizations the graph would
          connect.
        </p>
      ) : null}
      <p className={HINT}>It&apos;s included on the Business and Enterprise plans.</p>
    </section>
  );
};

const Entities: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.entityGraph);
  const enabled = Boolean(workspaceId && capability.granted);
  const [kind, setKind] = useState<EntityKind | undefined>(undefined);
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const params = { kind, q: q || undefined, page, page_size: PAGE_SIZE };

  const summary = useQuery({ queryKey: entityKeys.summary(workspaceId), queryFn: () => getEntitySummary(workspaceId), enabled });
  const list = useQuery({ queryKey: entityKeys.list(workspaceId, params), queryFn: () => listEntities(workspaceId, params), enabled });

  if (capability.isLoading) {
    return <Loader2 className="mx-auto mt-10 h-5 w-5 animate-spin text-muted-foreground" aria-label="Loading" />;
  }
  if (!capability.granted) {
    return <LockedView workspaceId={workspaceId} />;
  }
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center gap-3">
        <Network className="h-5 w-5 text-muted-foreground" aria-hidden />
        <h1 className={PAGE_TITLE}>Entity graph</h1>
        {summary.data && summary.data.open_reviews > 0 && (
          <Link to={verificationPath(orgSlug, workspaceSlug)} className="ml-auto text-sm font-semibold text-primary hover:underline">
            {summary.data.open_reviews} possible duplicate{summary.data.open_reviews === 1 ? "" : "s"} to review
            {summary.data.open_conflicts > 0 ? ` (${summary.data.open_conflicts} with conflicting identifiers)` : ""}
          </Link>
        )}
      </header>

      {summary.data && (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Summary">
          <div className={`${SURFACE_INSET} p-3`}>
            <p className={HINT}>Records</p>
            <p className="text-lg font-semibold">{summary.data.records.toLocaleString()}</p>
          </div>
          <div className={`${SURFACE_INSET} p-3`}>
            <p className={HINT}>Documents linked</p>
            <p className="text-lg font-semibold">{summary.data.documents_linked.toLocaleString()}</p>
          </div>
          {summary.data.models.map((model) => (
            <div key={model.kind} className={`${SURFACE_INSET} p-3`}>
              <p className={HINT}>{model.kind.toLowerCase()} matching</p>
              <p className="text-sm font-semibold">
                {model.source === "FITTED" ? `Learned v${model.version} from ${model.pair_count.toLocaleString()} pairs` : "Platform defaults"}
              </p>
            </div>
          ))}
        </section>
      )}

      <section className={`${SURFACE} space-y-3 p-4`}>
        <form
          role="search"
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setQ(draft.trim());
            setPage(1);
          }}
        >
          <label htmlFor="entity-search" className="sr-only">
            Search by name or identifier
          </label>
          <div className="relative min-w-[16rem] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
            <input
              id="entity-search"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Name, email, PAN, GSTIN, IBAN…"
              className={`${INPUT} pl-8`}
            />
          </div>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Kind">
            <button
              type="button"
              aria-pressed={kind === undefined}
              onClick={() => {
                setKind(undefined);
                setPage(1);
              }}
              className={`rounded-full px-3 py-1 text-xs font-medium ${kind === undefined ? "bg-primary text-primary-foreground" : "bg-muted"}`}
            >
              All
            </button>
            {ENTITY_KINDS.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={kind === value}
                onClick={() => {
                  setKind(value);
                  setPage(1);
                }}
                className={`rounded-full px-3 py-1 text-xs font-medium ${kind === value ? "bg-primary text-primary-foreground" : "bg-muted"}`}
              >
                {ENTITY_KIND_LABELS[value]}
                {summary.data ? ` ${summary.data.counts_by_kind[value] ?? 0}` : ""}
              </button>
            ))}
          </div>
        </form>
        {list.data?.matched_by_identifier && (
          <p className={HINT}>Matched by identifier. The value you typed was compared as a keyed hash and is not stored.</p>
        )}
        {list.isError ? (
          <ErrorState
            title="Could not load records"
            description={errorMessage(list.error, "The server did not return the records.")}
            onRetry={() => void list.refetch()}
          />
        ) : (
          <div className={SCROLL_X}>
            <table className="w-full text-sm">
              <thead>
                <tr className={TABLE_HEAD}>
                  <th className="px-3 py-2 text-left">Name</th>
                  <th className="px-3 py-2 text-left">Kind</th>
                  <th className="px-3 py-2 text-right">Documents</th>
                  <th className="px-3 py-2 text-left">Identifiers</th>
                  <th className="px-3 py-2 text-left">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {(list.data?.items ?? []).map((row) => (
                  <tr key={row.id} className={TABLE_ROW}>
                    <td className="px-3 py-2">
                      <Link to={entityPath(orgSlug, workspaceSlug, row.id)} className="font-medium text-primary hover:underline">
                        {row.display_name}
                      </Link>
                      {row.merged_records > 0 && <span className={`${HINT} ml-2`}>+{row.merged_records} merged</span>}
                    </td>
                    <td className="px-3 py-2">{row.kind.toLowerCase()}</td>
                    <td className="px-3 py-2 text-right">{row.documents}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">{row.identifier_kinds.join(", ") || "—"}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {row.last_seen_at ? formatTimestampDate(row.last_seen_at) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {list.isLoading && <Loader2 className="mx-auto my-4 h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" />}
            {list.data && list.data.items.length === 0 && (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {q ? "Nothing matches that search." : "No records yet. They appear as documents are processed."}
              </p>
            )}
          </div>
        )}
        {list.data && pages > 1 && (
          <nav className="flex items-center justify-end gap-2 text-sm" aria-label="Pages">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded px-2 py-1 hover:bg-muted disabled:opacity-40">
              Previous
            </button>
            <span className={HINT}>
              Page {page} of {pages}
            </span>
            <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} className="rounded px-2 py-1 hover:bg-muted disabled:opacity-40">
              Next
            </button>
          </nav>
        )}
      </section>
    </div>
  );
};

export default Entities;
