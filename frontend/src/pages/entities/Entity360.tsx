/**
 * ARCH42-S2:page-360 — one canonical record: who or what it is, every
 * document that names it (as a timeline), its identifiers (masked; the server
 * never returns more), its relationships, the records merged into it (each
 * one undoable), and the graph around it. Splitting moves chosen documents to
 * a new record; the pair is remembered as separate so no sweep re-merges it.
 * ARCH46-S2:entity-obligations — the Obligations section lists every
 * obligation tied to this record (or a record merged into it).
 */
import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Loader2, Lock, Network, Scissors, Trash2, Undo2 } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { GraphExplorer } from "@/components/entities/GraphExplorer";
import EntityObligations from "@/components/obligations/EntityObligations";
import { ErrorState } from "@/components/common/ErrorState";
import { BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_SECONDARY, HINT, INPUT, PAGE_TITLE, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { entitiesPath, entityPath, verificationPath, workItemDetailsPath } from "@/routes/tenantPaths";
import {
  entityKeys,
  eraseEntity,
  getEntity,
  getEntityGraph,
  renameEntity,
  splitEntity,
  unmergeEntity,
} from "@/services/api/entities";
import { errorMessage } from "@/services/api/errors";
import { RELATION_LABELS } from "@/types/entities";
// ARCH46-S2:h8-timestamps — instants follow the reader's profile zone and language (ARCH-30 D-5; verify_arch30_tranche3 H8).
import { formatTimestampDate } from "@/utils/displayTime";

const DECISION_LABEL: Readonly<Record<string, string>> = {
  AUTO: "linked automatically",
  REVIEW: "awaiting review",
  CONFIRMED: "confirmed by a reviewer",
  REJECTED: "rejected",
};

const METHOD_LABEL: Readonly<Record<string, string>> = {
  IDENTIFIER: "matched by identifier",
  MODEL: "matched by name and details",
  NEW: "first seen here",
  MANUAL: "set by a reviewer",
};

const Section: React.FC<{ readonly title: string; readonly children: React.ReactNode; readonly action?: React.ReactNode }> = ({
  title,
  children,
  action,
}) => (
  <section className={`${SURFACE} space-y-3 p-4`} aria-label={title}>
    <div className="flex items-center gap-2">
      <h2 className={SECTION_TITLE}>{title}</h2>
      <div className="ml-auto">{action}</div>
    </div>
    {children}
  </section>
);

const Entity360: React.FC = () => {
  const { entityId = "", orgSlug = "", workspaceSlug = "" } = useParams<{ entityId: string; orgSlug: string; workspaceSlug: string }>();
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const role = workspace?.role;
  const canEdit = role === "ADMIN" || role === "OWNER" || role === "CONTRIBUTOR";
  const isAdmin = role === "ADMIN" || role === "OWNER";
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.entityGraph);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const enabled = Boolean(workspaceId && entityId && capability.granted);
  const [depth, setDepth] = useState(2);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [naming, setNaming] = useState<string | null>(null);

  const detail = useQuery({ queryKey: entityKeys.detail(workspaceId, entityId), queryFn: () => getEntity(workspaceId, entityId), enabled });
  const graph = useQuery({
    queryKey: entityKeys.graph(workspaceId, entityId, depth),
    queryFn: () => getEntityGraph(workspaceId, entityId, depth),
    enabled,
  });
  // A merged record's URL opens its surviving root.
  const rootId = detail.data?.entity.id;
  useEffect(() => {
    if (rootId && rootId !== entityId) {
      navigate(entityPath(orgSlug, workspaceSlug, rootId), { replace: true });
    }
  }, [entityId, navigate, orgSlug, rootId, workspaceSlug]);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: entityKeys.all(workspaceId) });
  };
  const unmerge = useMutation({ mutationFn: (id: string) => unmergeEntity(workspaceId, id), onSuccess: refresh });
  const split = useMutation({
    mutationFn: () => splitEntity(workspaceId, entityId, [...selected]),
    onSuccess: (row) => {
      setSelected(new Set());
      refresh();
      navigate(entityPath(orgSlug, workspaceSlug, row.id));
    },
  });
  const rename = useMutation({
    mutationFn: (name: string) => renameEntity(workspaceId, entityId, name),
    onSuccess: () => {
      setNaming(null);
      refresh();
    },
  });
  const erase = useMutation({
    mutationFn: () => eraseEntity(workspaceId, entityId),
    onSuccess: () => {
      refresh();
      navigate(entitiesPath(orgSlug, workspaceSlug));
    },
  });

  if (!capability.isLoading && !capability.granted) {
    return (
      <section className={`${SURFACE} mx-auto max-w-xl space-y-2 p-6`}>
        <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
        <p className="text-sm">The entity graph is included on the Business and Enterprise plans.</p>
      </section>
    );
  }
  if (detail.isError) {
    return <ErrorState
        title="Could not load this record"
        description={errorMessage(detail.error, "The server did not return this record.")}
        onRetry={() => void detail.refetch()}
      />;
  }
  if (!detail.data) {
    return <Loader2 className="mx-auto mt-10 h-5 w-5 animate-spin text-muted-foreground" aria-label="Loading" />;
  }
  const data = detail.data;
  const toggle = (id: string): void => {
    const next = new Set(selected);
    if (next.has(id)) {next.delete(id);}
    else {next.add(id);}
    setSelected(next);
  };

  return (
    <div className="space-y-5">
      <Link to={entitiesPath(orgSlug, workspaceSlug)} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Entity graph
      </Link>
      <header className="flex flex-wrap items-center gap-3">
        <Network className="h-5 w-5 text-muted-foreground" aria-hidden />
        {naming === null ? (
          <h1 className={PAGE_TITLE}>{data.entity.display_name}</h1>
        ) : (
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (naming.trim()) {rename.mutate(naming.trim());}
            }}
          >
            <label htmlFor="entity-name" className="sr-only">
              Name
            </label>
            <input id="entity-name" value={naming} onChange={(event) => setNaming(event.target.value)} className={INPUT} />
            <button type="submit" className={BUTTON_SECONDARY} disabled={rename.isPending}>
              Save
            </button>
            <button type="button" className={BUTTON_GHOST} onClick={() => setNaming(null)}>
              Cancel
            </button>
          </form>
        )}
        <span className="rounded bg-muted px-2 py-0.5 text-xs font-medium">{data.entity.kind.toLowerCase()}</span>
        <span className={HINT}>
          {data.entity.documents} document{data.entity.documents === 1 ? "" : "s"}
          {data.first_seen_at ? ` · first seen ${formatTimestampDate(data.first_seen_at)}` : ""}
        </span>
        <div className="ml-auto flex gap-2">
          {canEdit && naming === null && ["PERSON", "ORGANIZATION", "ADDRESS"].includes(data.entity.kind) && (
            <button type="button" className={BUTTON_GHOST} onClick={() => setNaming(data.entity.display_name)}>
              Rename
            </button>
          )}
          {isAdmin && (
            <button
              type="button"
              className={BUTTON_DESTRUCTIVE}
              disabled={erase.isPending}
              onClick={() => {
                if (window.confirm("Erase this record, its identifiers and its links to every document? The documents themselves are kept.")) {
                  erase.mutate();
                }
              }}
            >
              <Trash2 className="mr-1 inline h-4 w-4" aria-hidden />
              Erase record
            </button>
          )}
        </div>
      </header>
      {[unmerge, split, rename, erase].some((m) => m.isError) && (
        <p role="alert" className="text-sm text-destructive">
          {errorMessage([unmerge, split, rename, erase].find((m) => m.isError)?.error, "That change could not be made.")}
        </p>
      )}
      {data.open_candidates.length > 0 && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
          {data.open_candidates.length} possible duplicate{data.open_candidates.length === 1 ? "" : "s"} of this record{" "}
          {data.open_candidates.some((c) => c.reason === "CONFLICT") ? "(with conflicting identifiers) " : ""}await a decision in the{" "}
          <Link to={verificationPath(orgSlug, workspaceSlug)} className="font-semibold text-primary hover:underline">
            review hub
          </Link>
          .
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Identifiers">
          {data.identifiers.length === 0 ? (
            <p className={HINT}>No identifiers yet.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {data.identifiers.map((identifier) => (
                <li key={identifier.id} className="flex items-center gap-2">
                  <span className="w-40 text-xs font-medium text-muted-foreground">{identifier.kind.replace(/_/g, " ").toLowerCase()}</span>
                  <span className="font-mono">{identifier.display}</span>
                  {identifier.derived && <span className={HINT}>(from GSTIN)</span>}
                </li>
              ))}
            </ul>
          )}
          <p className={HINT}>Identifiers are stored as keyed hashes and shown masked.</p>
        </Section>

        <Section title="Relationships">
          {data.relationships.length === 0 ? (
            <p className={HINT}>No relationships found yet.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {data.relationships.map((rel) => (
                <li key={`${rel.relation}-${rel.direction}-${rel.other_id}`}>
                  {rel.direction === "out" ? "" : "← "}
                  <span className="text-muted-foreground">{RELATION_LABELS[rel.relation] ?? rel.relation.toLowerCase()}</span>{" "}
                  <Link to={entityPath(orgSlug, workspaceSlug, rel.other_id)} className="font-medium text-primary hover:underline">
                    {rel.other_name}
                  </Link>{" "}
                  <span className={HINT}>
                    ({rel.documents} document{rel.documents === 1 ? "" : "s"})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <Section
        title="Graph"
        action={
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Depth
            <select value={depth} onChange={(event) => setDepth(Number(event.target.value))} className="rounded border border-border bg-background px-1 py-0.5">
              {[1, 2, 3].map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
        }
      >
        {graph.data ? (
          <GraphExplorer graph={graph.data} onOpen={(id) => navigate(entityPath(orgSlug, workspaceSlug, id))} />
        ) : (
          <Loader2 className="mx-auto h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading graph" />
        )}
      </Section>

      <Section
        title="Documents"
        action={
          canEdit && selected.size > 0 ? (
            <button type="button" className={BUTTON_SECONDARY} disabled={split.isPending} onClick={() => split.mutate()}>
              <Scissors className="mr-1 inline h-4 w-4" aria-hidden />
              Split {selected.size} into a new record
            </button>
          ) : null
        }
      >
        <ol className="space-y-2">
          {data.documents.map((doc) => (
            <li key={doc.mention_id} className="flex items-start gap-3 border-l-2 border-border pl-3 text-sm">
              {canEdit && (
                <input
                  type="checkbox"
                  aria-label={`Select ${doc.filename}`}
                  checked={selected.has(doc.mention_id)}
                  onChange={() => toggle(doc.mention_id)}
                  className="mt-1"
                />
              )}
              <div>
                <Link to={workItemDetailsPath(orgSlug, workspaceSlug, doc.work_item_id)} className="font-medium text-primary hover:underline">
                  {doc.filename}
                </Link>
                <p className={HINT}>
                  {formatTimestampDate(doc.created_at)} · as {doc.role.replace(/_/g, " ")} · {METHOD_LABEL[doc.method] ?? doc.method}
                  {doc.probability !== null && doc.probability !== undefined && doc.method === "MODEL"
                    ? ` (${Math.round(doc.probability * 100)}%)`
                    : ""}{" "}
                  · {DECISION_LABEL[doc.decision] ?? doc.decision}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Merged records">
          {data.members.length === 0 ? (
            <p className={HINT}>Nothing has been merged into this record.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {data.members.map((member) => (
                <li key={member.id} className="flex items-center gap-2">
                  <span>{member.display_name}</span>
                  <span className={HINT}>
                    {member.mentions} mention{member.mentions === 1 ? "" : "s"} · {member.merge_reason?.toLowerCase() ?? "merged"}
                    {member.merged_at ? ` · ${formatTimestampDate(member.merged_at)}` : ""}
                  </span>
                  {canEdit && (
                    <button
                      type="button"
                      className="ml-auto text-xs font-semibold text-primary hover:underline"
                      disabled={unmerge.isPending}
                      onClick={() => unmerge.mutate(member.id)}
                    >
                      <Undo2 className="mr-1 inline h-3 w-3" aria-hidden />
                      Undo merge
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="Obligations">
          <EntityObligations entityId={data.entity.id} orgSlug={orgSlug} workspaceSlug={workspaceSlug} />
        </Section>
      </div>
    </div>
  );
};

export default Entity360;
