/**
 * ARCH49-S2:conformance-panel — the workspace's flows (ARCH-37) and case
 * templates (ARCH-43) replayed, token by token, against what actually
 * happened. Fitness 1.0 means every run followed the model; the missing and
 * remaining counts say where runs departed from it. Runs started before a
 * flow was last edited are not held against the new graph.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { activityLabel, pct } from "@/components/process/common";
import { HINT, SCROLL_X, SECTION_TITLE, SURFACE, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { getConformance, processKeys } from "@/services/api/process";
import { errorMessage } from "@/services/api/errors";

const Fitness: React.FC<{ readonly value: number | null }> = ({ value }) =>
  value === null ? <span className={HINT}>—</span> : (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className={`block h-full ${value >= 0.95 ? "bg-emerald-500" : value >= 0.8 ? "bg-amber-500" : "bg-destructive"}`} style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="tabular-nums">{value.toFixed(3)}</span>
    </span>
  );

const Places: React.FC<{ readonly places: Readonly<Record<string, number>> }> = ({ places }) => {
  const entries = Object.entries(places);
  return entries.length ? (
    <span className="text-xs">{entries.slice(0, 3).map(([k, n]) => `${activityLabel(k)} ×${n}`).join(" · ")}</span>
  ) : <span className={HINT}>—</span>;
};

export const ConformancePanel: React.FC<{ readonly workspaceId: string; readonly days: number }> = ({ workspaceId, days }) => {
  const q = useQuery({ queryKey: processKeys.conformance(workspaceId, days), queryFn: () => getConformance(workspaceId, days), enabled: Boolean(workspaceId) });
  if (q.isLoading) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (q.isError || !q.data) {
    return <p className="text-sm text-destructive">{errorMessage(q.error, "Could not replay the models.")}</p>;
  }
  return (
    <div className="space-y-4">
      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="conf-flows">
        <h2 id="conf-flows" className={SECTION_TITLE}>Flows</h2>
        {q.data.flows.length === 0 ? <p className={HINT}>No flow ran in the last {q.data.window_days} days.</p> : (
          <div className={SCROLL_X}>
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className={TABLE_HEAD}>
                  <th className="px-2 py-2">Flow</th><th className="px-2 py-2">Fitness</th><th className="px-2 py-2">Fitting runs</th>
                  <th className="px-2 py-2">Missing at</th><th className="px-2 py-2">Left over at</th><th className="px-2 py-2">Not replayed</th>
                </tr>
              </thead>
              <tbody>
                {q.data.flows.map((f) => (
                  <tr key={f.rule_id} className={TABLE_ROW}>
                    <td className="px-2 py-2 font-semibold">{f.name}{f.is_active ? "" : <span className={`${HINT} ml-1`}>(paused)</span>}</td>
                    <td className="px-2 py-2"><Fitness value={f.fitness} /></td>
                    <td className="px-2 py-2 tabular-nums">{f.fitting} / {f.replayed}</td>
                    <td className="px-2 py-2"><Places places={f.missing_at} /></td>
                    <td className="px-2 py-2"><Places places={f.remaining_at} /></td>
                    <td className="px-2 py-2 text-xs text-muted-foreground">
                      {f.error ?? `${f.in_progress} running · ${f.graph_changed_since} before the last edit`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="conf-templates">
        <h2 id="conf-templates" className={SECTION_TITLE}>Case templates</h2>
        {q.data.templates.length === 0 ? <p className={HINT}>No case was opened in the last {q.data.window_days} days.</p> : (
          <div className={SCROLL_X}>
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className={TABLE_HEAD}>
                  <th className="px-2 py-2">Template</th><th className="px-2 py-2">Fitness</th><th className="px-2 py-2">Fitting cases</th>
                  <th className="px-2 py-2">Missing at</th><th className="px-2 py-2">Re-evaluated after an inconsistency</th><th className="px-2 py-2">Open</th>
                </tr>
              </thead>
              <tbody>
                {q.data.templates.map((t) => (
                  <tr key={t.template_id} className={TABLE_ROW}>
                    <td className="px-2 py-2 font-semibold">{t.name} <span className={HINT}>v{t.version}</span></td>
                    <td className="px-2 py-2"><Fitness value={t.fitness} /></td>
                    <td className="px-2 py-2 tabular-nums">{t.fitting} / {t.replayed}</td>
                    <td className="px-2 py-2"><Places places={t.missing_at} /></td>
                    <td className="px-2 py-2 tabular-nums">{t.inconsistent_evaluations}</td>
                    <td className="px-2 py-2 tabular-nums">{t.in_progress} <span className={HINT}>of {t.cases} ({pct(t.cases ? t.in_progress / t.cases : 0)})</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default ConformancePanel;
