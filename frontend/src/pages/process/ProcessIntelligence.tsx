/**
 * ARCH49-S2:page-process — Process Intelligence & the Governed Exception Agent.
 *
 *   Overview     the object-centric event log: objects and events per type, which
 *                types share events, how fresh each source is, the breach models,
 *                what is at risk and what the agent has waiting
 *   Discovery    the directly-follows graph and the variants of one object type,
 *                each variant with its throughput, cost-to-serve (from the cost
 *                truth) and people's touches; open any object's timeline
 *   Conformance  flows and case templates replayed on the log (token replay)
 *   Service      SLA policies, the breach models (Brier-checked on held-out
 *   levels       objects, refused when they do not beat the base rate), predictions
 *   Cost         cost-to-serve per object type, reconciled where invoices were
 *   Agent        the exception agent's proposals and its policy
 *
 * Locked without the process-intelligence capability (Enterprise). The agent's
 * routes need a contributor; the policy and the service levels an admin.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Activity, Loader2, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";

import { AgentInbox } from "@/components/process/AgentInbox";
import { AgentPolicyPanel } from "@/components/process/AgentPolicyPanel";
import { CostCell, ProcessLocked, activityLabel, canAdminister, canContribute, duration, num, pct, usd } from "@/components/process/common";
import { ConformancePanel } from "@/components/process/ConformancePanel";
import { DfgGraph } from "@/components/process/DfgGraph";
import { ObjectTimeline } from "@/components/process/ObjectTimeline";
import { ModelRunCard, SlaPanel } from "@/components/process/SlaPanel";
import { CAPABILITY } from "@/constants/capabilities";
import {
  BUTTON_SECONDARY, HINT, SCROLL_X, SECTION_TITLE, SELECT, SURFACE, TABLE_HEAD, TABLE_ROW,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { processPath, processProposalPath } from "@/routes/tenantPaths";
import { getCost, getDiscovery, getProcessOverview, processKeys, sweepNow } from "@/services/api/process";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import { OBJECT_TYPES, OBJECT_TYPE_LABELS, STATUS_LABELS, type ObjectType, type ProposalStatus } from "@/types/process";

type Tab = "overview" | "discovery" | "conformance" | "sla" | "cost" | "agent";

const TABS: readonly (readonly [Tab, string])[] = [
  ["overview", "Overview"], ["discovery", "Discovery"], ["conformance", "Conformance"], ["sla", "Service levels"],
  ["cost", "Cost to serve"], ["agent", "Exception agent"],
];
const WINDOWS: readonly number[] = [7, 30, 90, 180, 365];

const ProcessIntelligence: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "", proposalId } = useParams<{ orgSlug: string; workspaceSlug: string; proposalId?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.processIntelligence);
  const isAdmin = canAdminister(workspace?.role);
  const canAct = canContribute(workspace?.role);
  const [tab, setTab] = useState<Tab>(proposalId ? "agent" : "overview");
  const [objectType, setObjectType] = useState<ObjectType>("DOCUMENT");
  const [days, setDays] = useState(90);
  const [opened, setOpened] = useState<{ readonly type: ObjectType; readonly id: string } | null>(null);
  const enabled = Boolean(workspaceId && capability.granted);

  const overview = useQuery({
    queryKey: processKeys.overview(workspaceId), queryFn: () => getProcessOverview(workspaceId),
    enabled, refetchInterval: 60_000,
  });
  const discovery = useQuery({
    queryKey: processKeys.discovery(workspaceId, objectType, days), queryFn: () => getDiscovery(workspaceId, objectType, days),
    enabled: enabled && tab === "discovery",
  });
  const cost = useQuery({
    queryKey: processKeys.cost(workspaceId, days), queryFn: () => getCost(workspaceId, days),
    enabled: enabled && tab === "cost",
  });
  const sweep = useMutation({
    mutationFn: () => sweepNow(workspaceId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: processKeys.all(workspaceId) });
      toast.success("The log is up to date, the models refitted and the queue planned.");
    },
    onError: (error) => toast.error(errorMessage(error, "The sweep failed.")),
  });

  if (!capability.granted) {
    return capability.isLoading ? <Loader2 className="m-6 h-5 w-5 animate-spin" aria-label="Loading" /> : <ProcessLocked />;
  }
  const open = (type: ObjectType, id: string): void => setOpened({ type, id });
  const selectProposal = (id: string | null): void => {
    void navigate(id ? processProposalPath(orgSlug, workspaceSlug, id) : processPath(orgSlug, workspaceSlug), { replace: true });
  };
  const waiting = (overview.data?.proposals.PROPOSED ?? 0) + (overview.data?.proposals.AUTO_SCHEDULED ?? 0);

  return (
    <div className="space-y-4 p-4">
      <PageHeader
        icon={Activity}
        eyebrow="Workspace"
        title="Process intelligence"
        description="How work really flows, where it waits, what it costs — and an agent that proposes how to clear it."
        actions={isAdmin ? (
          <button type="button" className={BUTTON_SECONDARY} disabled={sweep.isPending} onClick={() => sweep.mutate()}>
            {sweep.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />} Sweep now
          </button>
        ) : null}
      />
      {overview.data && (overview.data.at_risk > 0 || waiting > 0) ? (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
          {overview.data.at_risk > 0 ? `${overview.data.at_risk} open object(s) are predicted to miss their service level. ` : ""}
          {waiting > 0 ? `${waiting} proposal(s) from the exception agent are waiting.` : ""}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2 border-b border-border/60" role="tablist" aria-label="Process intelligence views">
        {TABS.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`border-b-2 px-3 py-2 text-sm font-semibold ${tab === id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
            {label}
            {id === "agent" && waiting > 0 ? <span className="ml-1 rounded-full bg-primary/10 px-1.5 text-[11px]">{waiting}</span> : null}
          </button>
        ))}
      </div>
      {tab === "discovery" || tab === "conformance" || tab === "cost" ? (
        <div className="flex flex-wrap items-center gap-2">
          {tab === "discovery" ? (
            <select className={`${SELECT} w-48`} aria-label="Object type" value={objectType} onChange={(e) => setObjectType(e.target.value as ObjectType)}>
              {OBJECT_TYPES.map((t) => <option key={t} value={t}>{OBJECT_TYPE_LABELS[t]}</option>)}
            </select>
          ) : null}
          <select className={`${SELECT} w-40`} aria-label="Window" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {WINDOWS.map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
        </div>
      ) : null}
      {opened ? (
        <ObjectTimeline workspaceId={workspaceId} objectType={opened.type} objectId={opened.id} onClose={() => setOpened(null)} onOpen={open} />
      ) : null}

      {tab === "overview" ? (
        overview.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : overview.isError || !overview.data ? (
          <p className="text-sm text-destructive">{errorMessage(overview.error, "Could not load the overview.")}</p>
        ) : (
          <div className="space-y-4">
            <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="log">
              <h2 id="log" className={SECTION_TITLE}>The event log</h2>
              <p className="text-sm">
                {num(overview.data.events)} event(s)
                {overview.data.first_at ? ` from ${formatTimestamp(overview.data.first_at)} to ${formatTimestamp(overview.data.last_at)}` : ""}.
              </p>
              <div className={SCROLL_X}>
                <table className="w-full min-w-[600px] text-sm">
                  <thead>
                    <tr className={TABLE_HEAD}><th className="px-2 py-2">Object</th><th className="px-2 py-2">Objects</th><th className="px-2 py-2">Events</th><th className="px-2 py-2">Shares events with</th></tr>
                  </thead>
                  <tbody>
                    {overview.data.object_types.map((o) => (
                      <tr key={o.object_type} className={TABLE_ROW}>
                        <td className="px-2 py-2">
                          <button type="button" className="font-semibold text-primary hover:underline" onClick={() => { setObjectType(o.object_type as ObjectType); setTab("discovery"); }}>{o.label}</button>
                        </td>
                        <td className="px-2 py-2 tabular-nums">{num(o.objects)}</td>
                        <td className="px-2 py-2 tabular-nums">{num(o.events)}</td>
                        <td className="px-2 py-2 text-xs text-muted-foreground">
                          {Object.entries(o.shares_events_with).map(([t, n]) => `${OBJECT_TYPE_LABELS[t as ObjectType] ?? t} (${n})`).join(", ") || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <details>
                <summary className="cursor-pointer text-xs font-semibold text-muted-foreground">Sources</summary>
                <ul className="mt-1 grid gap-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
                  {overview.data.sources.map((s) => (
                    <li key={s.source}>
                      <span className="font-semibold">{s.source.toLowerCase().split("_").join(" ")}</span> · read {formatTimestamp(s.last_run_at)}
                      {s.caught_up ? "" : " · catching up"}
                    </li>
                  ))}
                </ul>
              </details>
            </section>
            <section className={`${SURFACE} p-4`} aria-labelledby="models">
              <h2 id="models" className={SECTION_TITLE}>Breach models</h2>
              <p className={HINT}>{overview.data.at_risk} at risk · {overview.data.breached} breached</p>
              <div className="mt-2 grid gap-4 md:grid-cols-3">
                {Object.entries(overview.data.runs).map(([type, run]) => <ModelRunCard key={type} objectType={type} run={run} />)}
              </div>
            </section>
            <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="agent-summary">
              <h2 id="agent-summary" className={SECTION_TITLE}>The exception agent</h2>
              <p className="text-sm">
                {overview.data.policy.planning_enabled ? "Proposing resolutions" : "Not proposing"} ·{" "}
                {overview.data.policy.auto_apply_enabled ? `bounded decisions apply themselves after ${overview.data.policy.hold_minutes} minute(s)` : "everything waits for a person"}
              </p>
              <p className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                {(Object.entries(overview.data.proposals) as [ProposalStatus, number][]).map(([s, n]) => <span key={s}>{STATUS_LABELS[s]}: {n}</span>)}
              </p>
            </section>
          </div>
        )
      ) : null}

      {tab === "discovery" ? (
        discovery.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : discovery.isError || !discovery.data ? (
          <p className="text-sm text-destructive">{errorMessage(discovery.error, "Could not mine the log.")}</p>
        ) : (
          <div className="space-y-4">
            <p className="text-sm">
              {num(discovery.data.graph.objects)} {OBJECT_TYPE_LABELS[objectType].toLowerCase()} · {discovery.data.variant_count} variant(s) ·
              median throughput {duration(discovery.data.throughput_seconds.median)} (p90 {duration(discovery.data.throughput_seconds.p90)}) ·
              cost to serve <CostCell cost={discovery.data.cost} /> each · {discovery.data.touches_per_object} touch(es) by people each
              {discovery.data.truncated ? <span className="text-amber-700 dark:text-amber-400"> · the window holds more events than one view reads; narrow it</span> : null}
            </p>
            <DfgGraph graph={discovery.data.graph} />
            <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="variants">
              <h2 id="variants" className={SECTION_TITLE}>Variants</h2>
              <div className={SCROLL_X}>
                <table className="w-full min-w-[900px] text-sm">
                  <thead>
                    <tr className={TABLE_HEAD}>
                      <th className="px-2 py-2">Path</th><th className="px-2 py-2">Share</th><th className="px-2 py-2">Median time</th>
                      <th className="px-2 py-2">Cost each</th><th className="px-2 py-2">Touches</th><th className="px-2 py-2">Examples</th>
                    </tr>
                  </thead>
                  <tbody>
                    {discovery.data.variants.map((v) => (
                      <tr key={v.id} className={TABLE_ROW}>
                        <td className="max-w-md px-2 py-2 text-xs">
                          {v.activities.map(activityLabel).join("  →  ")}{v.length > v.activities.length ? " …" : ""}
                        </td>
                        <td className="px-2 py-2 tabular-nums">{pct(v.share, 1)} <span className={HINT}>({v.count})</span></td>
                        <td className="px-2 py-2 tabular-nums">{duration(v.seconds.median)}</td>
                        <td className="px-2 py-2"><CostCell cost={v.cost} /></td>
                        <td className="px-2 py-2 tabular-nums">{v.touches}{v.agent_events ? <span className={HINT}> · agent {v.agent_events}</span> : null}</td>
                        <td className="px-2 py-2 text-xs">
                          {v.object_ids.slice(0, 3).map((id) => (
                            <button key={id} type="button" className="mr-1 font-mono text-primary hover:underline" onClick={() => open(objectType, id)}>{id.slice(0, 8)}</button>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )
      ) : null}

      {tab === "conformance" ? <ConformancePanel workspaceId={workspaceId} days={days} /> : null}
      {tab === "sla" ? <SlaPanel workspaceId={workspaceId} isAdmin={isAdmin} onOpenObject={open} /> : null}

      {tab === "cost" ? (
        cost.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : cost.isError || !cost.data ? (
          <p className="text-sm text-destructive">{errorMessage(cost.error, "Could not load the cost to serve.")}</p>
        ) : (
          <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="cost">
            <h2 id="cost" className={SECTION_TITLE}>Cost to serve, last {cost.data.window_days} days</h2>
            <p className={HINT}>
              What the platform spent on each object&apos;s documents (OCR, embeddings, model calls), from the cost truth: scaled to
              the supplier invoice where one was reconciled. Metered events with no settled cost are counted as unknown — never
              priced at zero. People&apos;s time is not money here; it is the “touches” count under Discovery.
            </p>
            <div className={SCROLL_X}>
              <table className="w-full min-w-[760px] text-sm">
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th className="px-2 py-2">Object</th><th className="px-2 py-2">Objects</th><th className="px-2 py-2">Total</th>
                    <th className="px-2 py-2">Each</th><th className="px-2 py-2">Reconciled</th><th className="px-2 py-2">Unknown</th><th className="px-2 py-2">Charged</th>
                  </tr>
                </thead>
                <tbody>
                  {OBJECT_TYPES.map((t) => {
                    const c = cost.data.by_object_type[t];
                    return c ? (
                      <tr key={t} className={TABLE_ROW}>
                        <td className="px-2 py-2 font-semibold">{OBJECT_TYPE_LABELS[t]}</td>
                        <td className="px-2 py-2 tabular-nums">{num(c.objects)}</td>
                        <td className="px-2 py-2 tabular-nums">{usd(c.reconciled_micros)}</td>
                        <td className="px-2 py-2 tabular-nums">{usd(c.mean_reconciled_micros)}</td>
                        <td className="px-2 py-2 tabular-nums">{pct(c.reconciled_share)}</td>
                        <td className="px-2 py-2 tabular-nums">{c.unknown_events} event(s) · {pct(c.unknown_cost_share)} of charges</td>
                        <td className="px-2 py-2 tabular-nums">{usd(c.revenue_micros)}{c.truncated ? <span className={HINT}> (partial)</span> : null}</td>
                      </tr>
                    ) : null;
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )
      ) : null}

      {tab === "agent" ? (
        canAct ? (
          <div className="space-y-4">
            <AgentInbox workspaceId={workspaceId} canAct={canAct} selectedId={proposalId} onSelect={selectProposal} />
            <AgentPolicyPanel workspaceId={workspaceId} isAdmin={isAdmin} />
          </div>
        ) : (
          <p className={HINT}>The exception agent&apos;s proposals are for the people who decide review items: contributors and admins.</p>
        )
      ) : null}
    </div>
  );
};

export default ProcessIntelligence;
