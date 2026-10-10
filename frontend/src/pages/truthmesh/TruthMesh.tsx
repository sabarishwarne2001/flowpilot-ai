/**
 * Phase 2 — TruthMesh: the workspace's documents as one connected, self-checking record.
 *
 *   Cockpit      the risk index, the money at risk, open conflicts by severity, the document graph
 *                (authority on top, bills at the bottom) and what needs a decision first
 *   Conflicts    the discrepancy matrix (conflicts against the documents they involve) and every
 *                conflict with its values side by side and its decision
 *   What-if      a condition on one document ("delivery delayed 14 days", "clause 4 invoked") and
 *                the ripple it sends through the documents that depend on it
 *
 * A document opens as its twin in a drawer (?doc=), a view is remembered in the address (?view=),
 * so every screen can be linked to. Contributors decide conflicts and links and run simulations;
 * viewers read.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  Activity,
  AlertOctagon,
  Banknote,
  Download,
  GitFork,
  Loader2,
  Lock,
  Network,
  Play,
  RefreshCw,
  Share2,
  Sparkles,
  Unlink,
} from "lucide-react";
import { toast } from "sonner";

import { CapabilityLoading } from "@/components/common/CapabilityLoading";
import { ViewPlansAction } from "@/components/billing/ViewPlansAction";
import { CAPABILITY } from "@/constants/capabilities";
import { ConflictCard } from "@/components/truthmesh/ConflictCard";
import { MeshGraph } from "@/components/truthmesh/MeshGraph";
import { RippleView, SeverityLegend, worstSeverity } from "@/components/truthmesh/RippleView";
import {
  KindChip,
  money,
  relativeTime,
  RISK_COLOR,
  RiskBadge,
  RiskGauge,
  SEVERITY_STYLE,
  SeverityBadge,
  StatCard,
} from "@/components/truthmesh/shared";
import { TwinDrawer } from "@/components/truthmesh/TwinDrawer";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { errorMessage } from "@/services/api/errors";
import {
  exportConflictsCsv,
  getGraph,
  getMatrix,
  getOverview,
  getSimulation,
  listMeshConflicts,
  listSimulations,
  meshKeys,
  rebuildMesh,
  runSimulation,
} from "@/services/api/truthmesh";
import {
  SCENARIO_LABELS,
  SEVERITIES,
  type MeshNode,
  type MeshOverview,
  type RippleNode,
  type Scenario,
  type Severity,
  type Simulation,
} from "@/types/truthmesh";
import { formatTimestamp } from "@/utils/displayTime";

type View = "cockpit" | "conflicts" | "whatif";
const VIEWS: readonly { id: View; label: string; Icon: React.ElementType }[] = [
  { id: "cockpit", label: "Cockpit", Icon: Activity },
  { id: "conflicts", label: "Conflicts", Icon: AlertOctagon },
  { id: "whatif", label: "What-if", Icon: Sparkles },
];

const LockedView: React.FC = () => (
  <section className="fp-card mx-auto mt-6 max-w-2xl space-y-3 p-6" aria-labelledby="truthmesh-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
      <h2 id="truthmesh-lock" className="text-[15px] font-semibold tracking-tight">TruthMesh</h2>
    </div>
    <p className="text-sm text-muted-foreground">
      See your documents as one connected record: which invoice bills which order under which agreement, where they
      contradict each other (amounts beyond authority, duplicate billing, changed payee accounts, conflicting terms),
      and what a delay or a terminated contract would set off.
    </p>
    <p className="text-xs text-muted-foreground">TruthMesh is included on the Business and Enterprise plans.</p>
    <ViewPlansAction />
  </section>
);

export const TruthMesh: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { workspaceRole } = useResolvedTenant();
  const canDecide = isAtLeast(workspaceRole, "CONTRIBUTOR");
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.truthmesh);
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const view = (VIEWS.some((v) => v.id === params.get("view")) ? params.get("view") : "cockpit") as View;
  const openDoc = params.get("doc");
  const [simOrigin, setSimOrigin] = useState<string | null>(params.get("origin"));

  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value) {
            next.set(key, value);
          } else {
            next.delete(key);
          }
          return next;
        },
        { replace: key === "doc" },
      ),
    [setParams],
  );

  const overview = useQuery({
    queryKey: meshKeys.overview(workspaceId),
    queryFn: () => getOverview(workspaceId),
    enabled: Boolean(workspaceId && capability.granted),
    refetchInterval: (query) => (query.state.data?.state.status === "BUILDING" ? 2_000 : false),
  });

  const rebuild = useMutation({
    mutationFn: () => rebuildMesh(workspaceId),
    onSuccess: async () => {
      toast.success("Rebuilding the mesh");
      await queryClient.invalidateQueries({ queryKey: meshKeys.all(workspaceId) });
    },
    onError: (error) => toast.error(errorMessage(error, "The rebuild could not be started.")),
  });

  // A workspace that has never been built is built on the first visit by someone who may.
  const status = overview.data?.state.status;
  useEffect(() => {
    if (status === "EMPTY" && canDecide && !rebuild.isPending && (overview.data?.documents ?? 0) === 0) {
      rebuild.mutate();
    }
    // Only when the state first reads EMPTY.
  }, [status]);

  // When a build finishes, every view refetches.
  const [lastStatus, setLastStatus] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (lastStatus === "BUILDING" && status === "READY") {
      void queryClient.invalidateQueries({ queryKey: meshKeys.all(workspaceId) });
    }
    setLastStatus(status);
  }, [status, lastStatus, queryClient, workspaceId]);

  if (capability.isLoading) {
    return <CapabilityLoading />;
  }
  if (!capability.granted) {
    return <LockedView />;
  }

  const data = overview.data;
  const building = status === "BUILDING" || rebuild.isPending;

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded-lg bg-primary/10 p-1.5 text-primary">
              <Network className="h-5 w-5" aria-hidden />
            </span>
            <h1 className="text-xl font-semibold tracking-tight">TruthMesh</h1>
            {data ? (
              <span
                className={`ml-1 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                  status === "FAILED" ? "border-destructive/40 text-destructive" : "border-border text-muted-foreground"
                }`}
                data-testid="mesh-state"
              >
                {building ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : <span className={`h-1.5 w-1.5 rounded-full ${status === "READY" ? "bg-emerald-500" : "bg-muted-foreground"}`} aria-hidden />}
                {building ? "Building…" : status === "FAILED" ? "Last build failed" : status === "READY" ? `Built ${relativeTime(data.state.last_built_at)}${data.state.build_ms !== null ? ` in ${data.state.build_ms} ms` : ""}` : "Not built yet"}
              </span>
            ) : null}
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Your documents as one connected record: what links them, where they contradict each other, and what a change
            in one would set off in the others. Built from what extraction already read, on the platform&apos;s own stack.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="fp-btn fp-btn-secondary inline-flex items-center gap-1.5"
            onClick={() => void exportConflictsCsv(workspaceId).catch((error) => toast.error(errorMessage(error, "The export failed.")))}
          >
            <Download className="h-4 w-4" aria-hidden />
            Audit export
          </button>
          {canDecide ? (
            <button type="button" className="fp-btn fp-btn-primary inline-flex items-center gap-1.5" disabled={building} onClick={() => rebuild.mutate()}>
              <RefreshCw className={`h-4 w-4 ${building ? "animate-spin" : ""}`} aria-hidden />
              Rebuild
            </button>
          ) : null}
        </div>
      </header>

      {status === "FAILED" && data?.state.error ? (
        <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          The last build failed: {data.state.error}
        </p>
      ) : null}

      <nav role="tablist" aria-label="TruthMesh views" className="flex gap-1 border-b border-border">
        {VIEWS.map(({ id, label, Icon }) => (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={view === id}
            onClick={() => setParam("view", id === "cockpit" ? null : id)}
            className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
              view === id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="h-4 w-4" aria-hidden />
            {label}
            {id === "conflicts" && data && data.open_conflicts > 0 ? (
              <span className="rounded-full bg-red-500/15 px-1.5 text-[11px] font-semibold text-red-700 dark:text-red-300">{data.open_conflicts}</span>
            ) : null}
          </button>
        ))}
      </nav>

      {overview.isError ? (
        <p className="text-sm text-destructive">{errorMessage(overview.error, "The mesh could not be loaded.")}</p>
      ) : !data ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Loading the mesh…
        </p>
      ) : view === "cockpit" ? (
        <Cockpit workspaceId={workspaceId} data={data} canDecide={canDecide} onOpen={(id) => setParam("doc", id)} />
      ) : view === "conflicts" ? (
        <ConflictsView workspaceId={workspaceId} canDecide={canDecide} onOpen={(id) => setParam("doc", id)} />
      ) : (
        <WhatIf
          workspaceId={workspaceId}
          canRun={canDecide}
          origin={simOrigin}
          onOrigin={(id) => {
            setSimOrigin(id);
            setParam("origin", id);
          }}
          onOpen={(id) => setParam("doc", id)}
        />
      )}

      {openDoc ? (
        <TwinDrawer
          workspaceId={workspaceId}
          workItemId={openDoc}
          canDecide={canDecide}
          onClose={() => setParam("doc", null)}
          onNavigate={(id) => setParam("doc", id)}
          onSimulate={(id) => {
            setSimOrigin(id);
            setParams((current) => {
              const next = new URLSearchParams(current);
              next.set("view", "whatif");
              next.set("origin", id);
              next.delete("doc");
              return next;
            });
          }}
        />
      ) : null}
    </div>
  );
};

// --------------------------------------------------------------------------- cockpit

const Cockpit: React.FC<{
  readonly workspaceId: string;
  readonly data: MeshOverview;
  readonly canDecide: boolean;
  readonly onOpen: (id: string) => void;
}> = ({ workspaceId, data, canDecide, onOpen }) => {
  const [onlyRisky, setOnlyRisky] = useState(false);
  const graph = useQuery({
    queryKey: meshKeys.graph(workspaceId, null, 0.4),
    queryFn: () => getGraph(workspaceId, { minStrength: 0.4 }),
    enabled: data.documents > 0,
  });
  const nodes = useMemo(() => {
    const all = graph.data?.nodes ?? [];
    if (!onlyRisky) {
      return all;
    }
    const risky = new Set(all.filter((n) => n.risk_score > 0).map((n) => n.work_item_id));
    for (const link of graph.data?.links ?? []) {
      if (risky.has(link.source) || risky.has(link.target)) {
        risky.add(link.source);
        risky.add(link.target);
      }
    }
    return all.filter((n) => risky.has(n.work_item_id));
  }, [graph.data, onlyRisky]);
  const visible = useMemo(() => new Set(nodes.map((n) => n.work_item_id)), [nodes]);
  const links = useMemo(
    () => (graph.data?.links ?? []).filter((l) => visible.has(l.source) && visible.has(l.target)),
    [graph.data, visible],
  );

  if (data.documents === 0) {
    return (
      <section className="fp-card flex flex-col items-center gap-2 px-6 py-12 text-center">
        <Network className="h-8 w-8 text-muted-foreground" aria-hidden />
        <h2 className="text-base font-semibold">No documents in the mesh yet</h2>
        <p className="max-w-md text-sm text-muted-foreground">
          Every document joins the mesh once extraction finishes. Upload orders, invoices, receipts and agreements, or
          rebuild if they are already here.
        </p>
      </section>
    );
  }

  const band = data.risk_band;
  const critical = data.by_severity.CRITICAL ?? 0;
  const high = data.by_severity.HIGH ?? 0;

  return (
    <div className="space-y-5">
      <section aria-label="The mesh at a glance" className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <div className="fp-card col-span-2 flex items-center gap-4 p-4 lg:col-span-1" data-testid="risk-index">
          <RiskGauge score={data.risk_index} />
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">Risk index</div>
            <div className={`text-lg font-semibold ${RISK_COLOR[band].text}`}>{RISK_COLOR[band].label}</div>
            <div className="text-xs text-muted-foreground">size-weighted, 0 to 100</div>
          </div>
        </div>
        <StatCard
          label="Money at risk"
          testId="money-at-risk"
          value={data.mixed_currencies ? "Mixed" : money(data.exposure_micros, data.exposure_currency)}
          accent={data.exposure_micros > 0 ? "text-red-600 dark:text-red-400" : undefined}
          icon={<Banknote className="h-4 w-4" aria-hidden />}
          hint="Payments that should not leave as they are"
        />
        <StatCard
          label="Open conflicts"
          testId="open-conflicts"
          value={data.open_conflicts}
          accent={critical > 0 ? "text-red-600 dark:text-red-400" : undefined}
          icon={<AlertOctagon className="h-4 w-4" aria-hidden />}
          hint={`${critical} critical · ${high} high`}
        />
        <StatCard
          label="Documents linked"
          value={`${data.documents - data.unlinked_documents}/${data.documents}`}
          icon={<Share2 className="h-4 w-4" aria-hidden />}
          hint={`${data.links} links in ${data.clusters} cluster${data.clusters === 1 ? "" : "s"}`}
        />
        <StatCard
          label="Unlinked"
          value={data.unlinked_documents}
          icon={<Unlink className="h-4 w-4" aria-hidden />}
          hint="Documents nothing connects to yet"
        />
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="fp-card min-w-0 space-y-3 p-4" aria-labelledby="mesh-graph-title">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 id="mesh-graph-title" className="text-[15px] font-semibold tracking-tight">Document graph</h2>
              <p className="text-xs text-muted-foreground">Authority at the top, what draws on it below. Click a document for its twin.</p>
            </div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input type="checkbox" checked={onlyRisky} onChange={(event) => setOnlyRisky(event.target.checked)} />
              Only documents with conflicts and their links
            </label>
          </div>
          {graph.isLoading ? (
            <div className="flex h-[520px] items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
              Laying out the graph…
            </div>
          ) : (
            <MeshGraph nodes={nodes} links={links} onSelect={onOpen} height={600} />
          )}
          {data.relations.length > 0 ? (
            <div className="flex flex-wrap gap-1.5" aria-label="Relations in the mesh">
              {data.relations.map((r) => (
                <span key={r.relation} className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                  {r.label} <span className="font-semibold tabular-nums text-foreground">{r.count}</span>
                </span>
              ))}
            </div>
          ) : null}
        </section>

        <aside className="min-w-0 space-y-5">
          <section className="fp-card p-4" aria-labelledby="top-risks">
            <h2 id="top-risks" className="mb-2 text-[15px] font-semibold tracking-tight">Highest risk</h2>
            {data.top_risks.length === 0 ? (
              <p className="text-sm text-muted-foreground">No document is at risk.</p>
            ) : (
              <ol className="space-y-1">
                {data.top_risks.map((n: MeshNode) => (
                  <li key={n.work_item_id}>
                    <button type="button" onClick={() => onOpen(n.work_item_id)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted/50">
                      <RiskBadge score={n.risk_score} />
                      <span className="min-w-0 flex-1 leading-tight">
                        <span className="block truncate text-sm font-medium">{n.title}</span>
                        {n.filename && n.filename !== n.title ? (
                          <span className="block truncate text-[11px] text-muted-foreground" title={n.filename}>
                            {n.filename}
                          </span>
                        ) : null}
                      </span>
                      <KindChip kind={n.kind} label={n.kind_label} />
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </section>
          <section className="fp-card p-4" aria-labelledby="conflict-mix">
            <h2 id="conflict-mix" className="mb-3 text-[15px] font-semibold tracking-tight">Open conflicts by kind</h2>
            {data.by_kind.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing contradicts anything.</p>
            ) : (
              <ul className="space-y-2">
                {data.by_kind.map((k) => {
                  const max = Math.max(...data.by_kind.map((x) => x.count));
                  return (
                    <li key={k.kind} className="text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate">{k.label}</span>
                        <span className="font-semibold tabular-nums">{k.count}</span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-primary/70" style={{ width: `${(k.count / max) * 100}%` }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </aside>
      </div>

      <section className="space-y-2" aria-labelledby="needs-decision">
        <div className="flex items-center justify-between">
          <h2 id="needs-decision" className="text-[15px] font-semibold tracking-tight">What needs a decision first</h2>
          <SeverityLegendInline counts={data.by_severity} />
        </div>
        {data.top_conflicts.length === 0 ? (
          <p className="fp-card px-4 py-6 text-sm text-muted-foreground">No open conflict. Every linked document agrees with the documents it depends on.</p>
        ) : (
          <div className="space-y-2">
            {data.top_conflicts.map((c, i) => (
              <ConflictCard key={c.id} workspaceId={workspaceId} conflict={c} canDecide={canDecide} onOpenDocument={onOpen} defaultOpen={i === 0} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
};

const SeverityLegendInline: React.FC<{ readonly counts: Readonly<Record<Severity, number>> }> = ({ counts }) => (
  <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
    {SEVERITIES.map((s) => (
      <span key={s} className="inline-flex items-center gap-1">
        <span className={`h-2 w-2 rounded-full ${SEVERITY_STYLE[s].dot}`} aria-hidden />
        {counts[s] ?? 0} {s.toLowerCase()}
      </span>
    ))}
  </div>
);

// --------------------------------------------------------------------------- conflicts

const ConflictsView: React.FC<{
  readonly workspaceId: string;
  readonly canDecide: boolean;
  readonly onOpen: (id: string) => void;
}> = ({ workspaceId, canDecide, onOpen }) => {
  const [status, setStatus] = useState("ACTIVE");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [layout, setLayout] = useState<"list" | "matrix">("list");
  const filters = { status, severity: severity || undefined };
  const list = useQuery({
    queryKey: meshKeys.conflicts(workspaceId, filters),
    queryFn: () => listMeshConflicts(workspaceId, filters),
  });
  const matrix = useQuery({
    queryKey: meshKeys.matrix(workspaceId),
    queryFn: () => getMatrix(workspaceId),
    enabled: layout === "matrix",
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border border-border p-0.5" role="group" aria-label="Layout">
          {(["list", "matrix"] as const).map((l) => (
            <button
              key={l}
              type="button"
              aria-pressed={layout === l}
              onClick={() => setLayout(l)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${layout === l ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              {l === "list" ? "Conflicts" : "Discrepancy matrix"}
            </button>
          ))}
        </div>
        {layout === "list" ? (
          <>
            {[
              ["ACTIVE", "Open"],
              ["RESOLVED", "Resolved"],
              ["DISMISSED", "Dismissed"],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={status === value}
                onClick={() => setStatus(value!)}
                className={`rounded-full border px-3 py-1 text-xs font-medium ${status === value ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"}`}
              >
                {label}
              </button>
            ))}
            <select
              aria-label="Severity"
              className="fp-input h-8 w-auto min-w-[9rem] py-0 text-xs"
              value={severity}
              onChange={(event) => setSeverity(event.target.value as Severity | "")}
            >
              <option value="">Every severity</option>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s.charAt(0) + s.slice(1).toLowerCase()}
                </option>
              ))}
            </select>
            {list.data ? <span className="text-xs text-muted-foreground">{list.data.total} conflict{list.data.total === 1 ? "" : "s"}</span> : null}
          </>
        ) : null}
      </div>

      {layout === "list" ? (
        list.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading conflicts…</p>
        ) : list.isError ? (
          <p className="text-sm text-destructive">{errorMessage(list.error, "Conflicts could not be loaded.")}</p>
        ) : (list.data?.items.length ?? 0) === 0 ? (
          <p className="fp-card px-4 py-6 text-sm text-muted-foreground">Nothing here.</p>
        ) : (
          <div className="space-y-2">
            {list.data!.items.map((c) => (
              <ConflictCard key={c.id} workspaceId={workspaceId} conflict={c} canDecide={canDecide} onOpenDocument={onOpen} />
            ))}
          </div>
        )
      ) : matrix.isLoading ? (
        <p className="text-sm text-muted-foreground">Building the matrix…</p>
      ) : !matrix.data || matrix.data.rows.length === 0 ? (
        <p className="fp-card px-4 py-6 text-sm text-muted-foreground">No open conflict, so the matrix is empty.</p>
      ) : (
        <div className="fp-card overflow-x-auto overscroll-x-contain" data-testid="discrepancy-matrix">
          <table className="min-w-full border-separate border-spacing-0 text-sm">
            <thead>
              <tr>
                <th className="sticky left-0 z-10 min-w-[18rem] border-b border-r border-border bg-card px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
                  Conflict
                </th>
                {matrix.data.columns.map((col) => (
                  <th key={col.work_item_id} className="min-w-[9.5rem] border-b border-border bg-muted/30 px-3 py-2 text-left align-bottom">
                    <button type="button" onClick={() => onOpen(col.work_item_id)} className="block max-w-[11rem] truncate text-xs font-semibold text-primary hover:underline" title={col.filename}>
                      {col.title}
                    </button>
                    <span className="text-[10.5px] font-normal text-muted-foreground">{col.kind_label}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.data.rows.map((row) => (
                <tr key={row.conflict.id} className="group">
                  <td className="sticky left-0 z-10 border-b border-r border-border bg-card px-3 py-2 align-top group-hover:bg-muted/40">
                    <div className="flex items-center gap-1.5">
                      <SeverityBadge severity={row.conflict.severity} compact />
                      <span className="text-[11px] font-medium text-muted-foreground">{row.conflict.kind_label}</span>
                    </div>
                    <div className="mt-0.5 text-[12.5px] font-medium leading-snug [overflow-wrap:anywhere]">{row.conflict.title}</div>
                  </td>
                  {matrix.data!.columns.map((col) => {
                    const cell = row.cells[col.work_item_id];
                    return (
                      <td
                        key={col.work_item_id}
                        className={`border-b border-border px-3 py-2 align-top text-xs group-hover:bg-muted/40 ${
                          cell ? (cell.role === "parent" ? "bg-sky-500/5" : "bg-red-500/[0.06]") : ""
                        }`}
                      >
                        {cell ? (
                          <>
                            <div className="font-mono font-semibold [overflow-wrap:anywhere]">{cell.display}</div>
                            {cell.role ? <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{cell.role}</div> : null}
                          </>
                        ) : (
                          <span className="text-muted-foreground/50">·</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// --------------------------------------------------------------------------- what-if

const DIMENSION_LABEL: Readonly<Record<string, string>> = { FINANCIAL: "Financial", OPERATIONAL: "Operational", LEGAL: "Legal" };

const WhatIf: React.FC<{
  readonly workspaceId: string;
  readonly canRun: boolean;
  readonly origin: string | null;
  readonly onOrigin: (id: string) => void;
  readonly onOpen: (id: string) => void;
}> = ({ workspaceId, canRun, origin, onOrigin, onOpen }) => {
  const queryClient = useQueryClient();
  const [scenario, setScenario] = useState<Scenario>("DELAY");
  const [days, setDays] = useState(14);
  const [percent, setPercent] = useState(8);
  const [clause, setClause] = useState("");
  const [result, setResult] = useState<Simulation | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  const graph = useQuery({ queryKey: meshKeys.graph(workspaceId, null, 0.4), queryFn: () => getGraph(workspaceId, { minStrength: 0.4 }) });
  const history = useQuery({ queryKey: meshKeys.simulations(workspaceId), queryFn: () => listSimulations(workspaceId) });
  const documents = useMemo(
    () => [...(graph.data?.nodes ?? [])].sort((a, b) => b.rank - a.rank || a.title.localeCompare(b.title)),
    [graph.data],
  );
  const selectedOrigin = origin ?? documents.find((d) => d.kind === "PURCHASE_ORDER")?.work_item_id ?? documents[0]?.work_item_id ?? "";

  const run = useMutation({
    mutationFn: () =>
      runSimulation(workspaceId, {
        work_item_id: selectedOrigin,
        scenario,
        ...(scenario === "DELAY" ? { days } : {}),
        ...(scenario === "AMOUNT_CHANGE" ? { percent } : {}),
        ...(scenario === "CLAUSE_INVOKED" ? { clause } : {}),
      }),
    onSuccess: async (sim) => {
      setResult(sim);
      setFocus(sim.result.origin);
      await queryClient.invalidateQueries({ queryKey: meshKeys.simulations(workspaceId) });
    },
    onError: (error) => toast.error(errorMessage(error, "The simulation did not run.")),
  });

  const loadOld = useMutation({
    mutationFn: (id: string) => getSimulation(workspaceId, id),
    onSuccess: (sim) => {
      setResult(sim);
      setFocus(sim.result.origin);
    },
  });

  const nodes = result?.result.nodes ?? [];
  const focused: RippleNode | undefined = nodes.find((n) => n.work_item_id === focus) ?? nodes[0];
  const summary = result?.result.summary;

  return (
    <div className="grid gap-5 xl:grid-cols-[22rem_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-4">
        <section className="fp-card space-y-3 p-4" aria-labelledby="whatif-form">
          <h2 id="whatif-form" className="text-[15px] font-semibold tracking-tight">Simulate a change</h2>
          <label className="block text-xs font-medium" htmlFor="whatif-origin">Starting document</label>
          <select id="whatif-origin" className="fp-input w-full" value={selectedOrigin} onChange={(event) => onOrigin(event.target.value)}>
            {documents.map((d) => (
              <option key={d.work_item_id} value={d.work_item_id}>
                {d.kind_label}: {d.title}
              </option>
            ))}
          </select>
          <fieldset>
            <legend className="mb-1.5 text-xs font-medium">What happens</legend>
            <div className="grid gap-1.5">
              {(Object.keys(SCENARIO_LABELS) as Scenario[]).map((s) => (
                <label
                  key={s}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-sm ${scenario === s ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"}`}
                >
                  <input type="radio" name="scenario" className="mt-1" checked={scenario === s} onChange={() => setScenario(s)} />
                  <span>
                    <span className="font-medium">{SCENARIO_LABELS[s].label}</span>
                    <span className="block text-xs text-muted-foreground">{SCENARIO_LABELS[s].hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {scenario === "DELAY" ? (
            <label className="block text-xs font-medium">
              Days late
              <input type="number" min={1} max={730} className="fp-input mt-1 w-full" value={days} onChange={(event) => setDays(Number(event.target.value))} />
            </label>
          ) : scenario === "AMOUNT_CHANGE" ? (
            <label className="block text-xs font-medium">
              Change (%)
              <input type="number" min={-95} max={500} step={0.5} className="fp-input mt-1 w-full" value={percent} onChange={(event) => setPercent(Number(event.target.value))} />
            </label>
          ) : scenario === "CLAUSE_INVOKED" ? (
            <label className="block text-xs font-medium">
              Clause (number or subject)
              <input className="fp-input mt-1 w-full" value={clause} placeholder="e.g. 4, or termination" onChange={(event) => setClause(event.target.value)} />
            </label>
          ) : null}
          <button
            type="button"
            className="fp-btn fp-btn-primary inline-flex w-full items-center justify-center gap-1.5"
            disabled={!canRun || !selectedOrigin || run.isPending || (scenario === "CLAUSE_INVOKED" && !clause.trim())}
            onClick={() => run.mutate()}
          >
            {run.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
            Run simulation
          </button>
          {!canRun ? <p className="text-xs text-muted-foreground">Contributors run simulations; you can read past ones.</p> : null}
        </section>

        <section className="fp-card p-4" aria-labelledby="whatif-history">
          <h2 id="whatif-history" className="mb-2 text-[15px] font-semibold tracking-tight">Recent simulations</h2>
          {(history.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">None yet.</p>
          ) : (
            <ul className="space-y-1">
              {history.data!.map((h) => (
                <li key={h.id}>
                  <button type="button" onClick={() => loadOld.mutate(h.id)} className="w-full rounded-md px-2 py-1.5 text-left hover:bg-muted/50">
                    <span className="block truncate text-sm font-medium">{h.title}</span>
                    <span className="text-[11px] text-muted-foreground">
                      {h.documents_affected} documents · {h.exposure_micros ? money(h.exposure_micros, h.currency) : "no money at risk"} · {formatTimestamp(h.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>

      <section className="min-w-0 space-y-4" aria-label="Simulation result" data-testid="ripple-result">
        {!result ? (
          <div className="fp-card flex flex-col items-center gap-2 px-6 py-16 text-center">
            <GitFork className="h-8 w-8 text-muted-foreground" aria-hidden />
            <h2 className="text-base font-semibold">Pick a document and a change</h2>
            <p className="max-w-md text-sm text-muted-foreground">
              The simulation follows the links from that document to everything that depends on it and reads each one&apos;s
              own terms: what money moves or loses its authority, which dates slip, which clauses are breached.
            </p>
          </div>
        ) : (
          <>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">{result.title}</h2>
              {result.result.clause ? (
                <p className="mt-1 text-sm text-muted-foreground">
                  {result.result.clause.found ? "Clause found: " : "Clause not found in the text; simulated from your words: "}
                  <span className="italic">“{result.result.clause.quote}”</span>
                </p>
              ) : null}
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard label="Documents affected" value={summary!.documents_affected} hint={`up to ${summary!.max_depth} links away`} testId="ripple-affected" />
              <StatCard
                label="Money at risk"
                value={money(summary!.financial_exposure_micros, summary!.currency)}
                accent={summary!.financial_exposure_micros > 0 ? "text-red-600 dark:text-red-400" : undefined}
                hint="Each payment counted once"
                testId="ripple-exposure"
              />
              <StatCard label="Breaches" value={summary!.breaches} accent={summary!.breaches > 0 ? "text-red-600 dark:text-red-400" : undefined} hint="Terms the change would break" />
              <StatCard
                label="Effects"
                value={summary!.effects.FINANCIAL + summary!.effects.OPERATIONAL + summary!.effects.LEGAL}
                hint={`${summary!.effects.FINANCIAL} financial · ${summary!.effects.OPERATIONAL} operational · ${summary!.effects.LEGAL} legal`}
              />
            </div>
            <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <div className="fp-card flex flex-col items-center gap-3 p-4">
                <RippleView nodes={nodes} selected={focused?.work_item_id ?? null} onSelect={setFocus} />
                <SeverityLegend />
              </div>
              <div className="min-w-0 space-y-2">
                {nodes.map((n) => {
                  const worst = worstSeverity(n);
                  return (
                    <article
                      key={n.work_item_id}
                      className={`fp-card p-3 ${focused?.work_item_id === n.work_item_id ? "ring-2 ring-primary/40" : ""}`}
                      data-testid="ripple-node"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <button type="button" onClick={() => setFocus(n.work_item_id)} className="flex min-w-0 items-center gap-2 text-left">
                          <KindChip kind={n.kind} label={n.kind_label} />
                          <span className="truncate text-sm font-semibold">{n.title}</span>
                        </button>
                        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                          {n.depth === 0 ? <span className="font-semibold text-primary">origin</span> : <span>{n.depth} link{n.depth === 1 ? "" : "s"} away · impact {Math.round(n.impact * 100)}%</span>}
                          {worst ? <SeverityBadge severity={worst} compact /> : null}
                          <button type="button" className="text-primary hover:underline" onClick={() => onOpen(n.work_item_id)}>
                            Twin
                          </button>
                        </div>
                      </div>
                      {n.effects.length === 0 ? (
                        <p className="mt-1 text-xs text-muted-foreground">Reached, with nothing in its own terms that the change touches.</p>
                      ) : (
                        <ul className="mt-2 space-y-1.5">
                          {n.effects.map((e, i) => (
                            <li key={i} className="rounded-md border border-border/60 bg-muted/20 px-2.5 py-1.5">
                              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                                <span className="rounded bg-background px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                                  {DIMENSION_LABEL[e.dimension]}
                                </span>
                                <span className={`h-1.5 w-1.5 rounded-full ${SEVERITY_STYLE[e.severity].dot}`} aria-hidden />
                                <span className="font-semibold text-foreground [overflow-wrap:anywhere]">{e.title}</span>
                                {e.breach ? <span className="rounded bg-red-500/15 px-1.5 text-[10.5px] font-bold text-red-700 dark:text-red-300">breach</span> : null}
                              </div>
                              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{e.detail}</p>
                              {e.quote ? <blockquote className="mt-1 border-l-2 border-primary/40 pl-2 text-[11.5px] italic text-muted-foreground [overflow-wrap:anywhere]">“{e.quote}”</blockquote> : null}
                            </li>
                          ))}
                        </ul>
                      )}
                    </article>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
};

export default TruthMesh;
