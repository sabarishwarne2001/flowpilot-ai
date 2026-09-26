/**
 * ARCH47-S2:page-erp — the workspace's ERP posting: the ledger (every posting,
 * filter by state, target and object, search by number or ERP reference),
 * approved outcomes ready to post, the targets they go to, and the lookup
 * tables mappings translate through. Locked without capability.erp_posting.
 */
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { BookUp, Loader2, Plus } from "lucide-react";

import { ErpLocked, PostingStateBadge, canAdminister, canContribute } from "@/components/erp/common";
import { LookupTables } from "@/components/erp/LookupTables";
import { NewTarget } from "@/components/erp/NewTarget";
import { PostingTable } from "@/components/erp/PostingTable";
import { ReadyToPost } from "@/components/erp/ReadyToPost";
import { CAPABILITY } from "@/constants/capabilities";
import { BUTTON_GHOST, BUTTON_PRIMARY, HINT, INPUT, PAGE_TITLE, SCROLL_X, SELECT, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { erpTargetPath, verificationPath } from "@/routes/tenantPaths";
import { erpKeys, getErpCatalog, listPostings, listTargets, type PostingFilters } from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";
import {
  ACK_LABELS, FORMAT_LABELS, OBJECT_KINDS, OBJECT_LABELS, PRESET_LABELS, STATE_LABELS, TRANSPORT_LABELS,
  type PostingState, type TargetDetail,
} from "@/types/erp";

type Tab = "postings" | "ready" | "targets" | "lookups";

const STATE_FILTERS: readonly { readonly id: string; readonly label: string }[] = [
  { id: "FAILED,REJECTED,MISMATCH,UNCERTAIN", label: "Needs a decision" },
  { id: "PENDING,SENDING,RETRYING,DELIVERED", label: "In flight" },
  { id: "DONE", label: STATE_LABELS.DONE },
  { id: "CANCELLED", label: STATE_LABELS.CANCELLED },
  { id: "", label: "All" },
];

const sumOf = (counts: Readonly<Record<string, number>>, ids: string): number =>
  ids.split(",").reduce((n, s) => n + (counts[s] ?? 0), 0);

const ErpPosting: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const navigate = useNavigate();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.erpPosting);
  const canPost = canContribute(workspace?.role);
  const isAdmin = canAdminister(workspace?.role);
  const [tab, setTab] = useState<Tab>("postings");
  const [state, setState] = useState("");
  const [targetId, setTargetId] = useState("");
  const [objectKind, setObjectKind] = useState("");
  const [q, setQ] = useState("");
  const [composing, setComposing] = useState(false);
  const enabled = Boolean(workspaceId && capability.granted);
  const filters: PostingFilters = {
    ...(state ? { state } : {}), ...(targetId ? { target_id: targetId } : {}), ...(objectKind ? { object_kind: objectKind } : {}),
    ...(q.trim() ? { q: q.trim() } : {}),
  };
  const postings = useQuery({
    queryKey: erpKeys.postings(workspaceId, filters),
    queryFn: () => listPostings(workspaceId, filters),
    enabled: enabled && tab === "postings",
    refetchInterval: 15_000,
  });
  const targets = useQuery({ queryKey: erpKeys.targets(workspaceId), queryFn: () => listTargets(workspaceId), enabled });
  const catalog = useQuery({ queryKey: erpKeys.catalog(workspaceId), queryFn: () => getErpCatalog(workspaceId), enabled, staleTime: 3_600_000 });
  const created = (detail: TargetDetail): void => {
    setComposing(false);
    navigate(erpTargetPath(orgSlug, workspaceSlug, detail.target.id));
  };

  if (!capability.granted) {
    return <ErpLocked />;
  }
  const counts = postings.data?.counts_by_state ?? {};
  const exceptions = sumOf(counts, "FAILED,REJECTED,MISMATCH,UNCERTAIN");
  const targetRows = targets.data?.items ?? [];
  return (
    <div className="space-y-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <BookUp className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>ERP posting</h1>
        <span className={HINT}>Approved outcomes to your system of record — exactly once, done only when the ERP says so.</span>
        {isAdmin && !composing ? (
          <button type="button" className={`${BUTTON_PRIMARY} ml-auto`} disabled={!catalog.data}
            onClick={() => { setTab("targets"); setComposing(true); }}>
            <Plus className="h-4 w-4" aria-hidden /> New target
          </button>
        ) : null}
      </header>
      {exceptions > 0 ? (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
          {exceptions} posting(s) failed, were rejected, were acknowledged differently or have an unknown outcome — a
          person decides each one.{" "}
          <Link className="font-semibold underline" to={verificationPath(orgSlug, workspaceSlug)}>Open the review hub</Link>
        </p>
      ) : null}
      {composing && catalog.data ? (
        <NewTarget workspaceId={workspaceId} catalog={catalog.data} onCreated={created} onCancel={() => setComposing(false)} />
      ) : null}
      {catalog.isError ? <p className="text-sm text-destructive">{errorMessage(catalog.error, "Could not load the catalog.")}</p> : null}
      <div className="flex flex-wrap gap-2 border-b border-border/60" role="tablist" aria-label="ERP posting views">
        {([["postings", "Postings"], ["ready", "Ready to post"], ["targets", "Targets"], ["lookups", "Lookup tables"]] as const).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`border-b-2 px-3 py-2 text-sm font-semibold ${tab === id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "postings" ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {STATE_FILTERS.map((f) => (
              <button key={f.label} type="button" aria-pressed={state === f.id} onClick={() => setState(f.id)}
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${state === f.id ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"}`}>
                {f.label}{f.id ? ` (${sumOf(counts, f.id)})` : ""}
              </button>
            ))}
            <select className={`${SELECT} w-52`} aria-label="Target" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">All targets</option>
              {targetRows.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <select className={`${SELECT} w-44`} aria-label="Object" value={objectKind} onChange={(e) => setObjectKind(e.target.value)}>
              <option value="">All objects</option>
              {OBJECT_KINDS.map((k) => <option key={k} value={k}>{OBJECT_LABELS[k]}</option>)}
            </select>
            <input className={`${INPUT} w-56`} value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} placeholder="Number or ERP reference" aria-label="Search" />
          </div>
          {postings.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
          {postings.isError ? <p className="text-sm text-destructive">{errorMessage(postings.error, "Could not load postings.")}</p> : null}
          {postings.data ? (
            <>
              <PostingTable rows={postings.data.items} />
              <p className={HINT}>{postings.data.total} posting(s)</p>
            </>
          ) : null}
        </div>
      ) : null}

      {tab === "ready" ? (
        targets.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : (
          <ReadyToPost workspaceId={workspaceId} targets={targetRows} canPost={canPost} />
        )
      ) : null}

      {tab === "targets" ? (
        <div className="space-y-3">
          {targets.isError ? <p className="text-sm text-destructive">{errorMessage(targets.error, "Could not load targets.")}</p> : null}
          {targetRows.length === 0 && !targets.isLoading ? (
            <p className={HINT}>No targets yet.{isAdmin ? " Add one with “New target”." : " A workspace admin adds them."}</p>
          ) : null}
          {targetRows.length > 0 ? (
            <div className={SCROLL_X}>
              <table className="w-full min-w-[760px] text-sm">
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th className="px-2 py-2">Target</th>
                    <th className="px-2 py-2">Format</th>
                    <th className="px-2 py-2">Delivery</th>
                    <th className="px-2 py-2">Done when</th>
                    <th className="px-2 py-2">Status</th>
                    <th className="px-2 py-2">Postings</th>
                    <th className="px-2 py-2">Needs a decision</th>
                  </tr>
                </thead>
                <tbody>
                  {targetRows.map((t) => (
                    <tr key={t.id} className={TABLE_ROW}>
                      <td className="px-2 py-2">
                        <Link className="font-semibold text-primary hover:underline" to={erpTargetPath(orgSlug, workspaceSlug, t.id)}>{t.name}</Link>
                        {t.host ? <p className="text-[11px] text-muted-foreground">{t.host}</p> : null}
                      </td>
                      <td className="px-2 py-2 text-xs">{t.preset !== "NONE" ? PRESET_LABELS[t.preset] : FORMAT_LABELS[t.format]}</td>
                      <td className="px-2 py-2 text-xs">{TRANSPORT_LABELS[t.transport]}</td>
                      <td className="px-2 py-2 text-xs">{ACK_LABELS[t.ack_mode]}</td>
                      <td className="px-2 py-2 text-xs">
                        {t.status === "ACTIVE" ? "Active" : "Disabled"}
                        {t.auto_post ? " · auto-post" : ""}
                        {t.transport !== "DOWNLOAD" && !t.credential_set ? <span className="text-destructive"> · no credential</span> : null}
                      </td>
                      <td className="px-2 py-2 tabular-nums">{t.postings}</td>
                      <td className="px-2 py-2">
                        {t.open_exceptions > 0 ? (
                          <button type="button" className={BUTTON_GHOST}
                            onClick={() => { setTargetId(t.id); setState("FAILED,REJECTED,MISMATCH,UNCERTAIN"); setTab("postings"); }}>
                            <PostingStateBadge state={"FAILED" as PostingState} /> {t.open_exceptions}
                          </button>
                        ) : <span className={HINT}>—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      {tab === "lookups" ? <LookupTables workspaceId={workspaceId} isAdmin={isAdmin} /> : null}
    </div>
  );
};

export default ErpPosting;
