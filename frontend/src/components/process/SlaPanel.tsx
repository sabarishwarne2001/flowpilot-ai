/**
 * ARCH49-S2:sla-panel — service levels, the breach model behind them, and what
 * is at risk now.
 *
 * Per object type a gradient-boosted classifier is trained on landmark
 * snapshots of finished objects and checked on objects it never saw (every
 * fifth, by id). It is used only if its Brier score beats the base rate's by a
 * margin; otherwise the run is REFUSED with the reason, and no prediction is
 * shown. The reliability table is the proof: in each band, how often the
 * predicted breaches actually happened.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { pct } from "@/components/process/common";
import { BUTTON_SECONDARY, FIELD_LABEL, HINT, INPUT, SCROLL_X, SECTION_TITLE, SURFACE, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { getSla, processKeys, setSlaPolicy } from "@/services/api/process";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import {
  OBJECT_TYPE_LABELS, PREDICTION_STATE_LABELS, type ModelRun, type ObjectType, type SlaObjectType, type SlaPolicy,
} from "@/types/process";

const STATE_TONES: Readonly<Record<string, string>> = {
  AT_RISK: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  BREACHED: "bg-destructive/10 text-destructive",
  OK: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
};

export const ModelRunCard: React.FC<{ readonly objectType: string; readonly run: ModelRun | null }> = ({ objectType, run }) => (
  <div className="space-y-1 text-sm">
    <p className="font-semibold">{OBJECT_TYPE_LABELS[objectType as ObjectType] ?? objectType}</p>
    {!run ? <p className={HINT}>Not trained yet.</p> : null}
    {run && run.status === "REFUSED" ? (
      <p className="text-xs text-muted-foreground">No prediction: {run.reason}</p>
    ) : null}
    {run && run.status === "ACCEPTED" ? (
      <>
        <p className="text-xs">
          Brier {run.brier?.toFixed(3)} vs {run.brier_baseline?.toFixed(3)} for the base rate ({pct(run.base_rate)} breach) ·
          skill {pct(run.skill)} · checked on {run.instances_holdout} held-out object(s)
        </p>
        {run.reliability.length ? (
          <table className="text-[11px]">
            <thead>
              <tr className="text-muted-foreground"><th className="pr-3 text-left">Predicted</th><th className="pr-3 text-left">Happened</th><th className="text-left">Snapshots</th></tr>
            </thead>
            <tbody>
              {run.reliability.map((b) => (
                <tr key={b.bin}><td className="pr-3 tabular-nums">{pct(b.predicted)}</td><td className="pr-3 tabular-nums">{pct(b.observed)}</td><td className="tabular-nums">{b.count}</td></tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <p className={HINT}>
          Strongest signals: {Object.entries(run.importances).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => k.split("_").join(" ")).join(", ") || "—"}
        </p>
      </>
    ) : null}
    {run ? <p className={HINT}>Trained {formatTimestamp(run.trained_at)} on {run.instances_train} object(s).</p> : null}
  </div>
);

const PolicyRow: React.FC<{ readonly workspaceId: string; readonly policy: SlaPolicy; readonly isAdmin: boolean }> = ({ workspaceId, policy, isAdmin }) => {
  const queryClient = useQueryClient();
  const [hours, setHours] = useState(policy.target_hours);
  const [risk, setRisk] = useState(Math.round(policy.at_risk_probability * 100));
  const [alerts, setAlerts] = useState(policy.alerts_enabled);
  const save = useMutation({
    mutationFn: () => setSlaPolicy(workspaceId, policy.object_type as SlaObjectType, {
      target_hours: hours, at_risk_probability: risk / 100, alerts_enabled: alerts,
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: processKeys.sla(workspaceId) });
      toast.success("Saved. The model is refitted on the next sweep.");
    },
    onError: (error) => toast.error(errorMessage(error, "Could not save.")),
  });
  const valid = Number.isInteger(hours) && hours >= 1 && hours <= 2160 && risk >= 1 && risk <= 99;
  return (
    <tr className={TABLE_ROW}>
      <td className="px-2 py-2 font-semibold">{OBJECT_TYPE_LABELS[policy.object_type as ObjectType] ?? policy.object_type}</td>
      <td className="px-2 py-2">
        <div className="flex w-32 items-center gap-1.5">
          <input type="number" className={INPUT} min={1} max={2160} value={hours} disabled={!isAdmin}
            aria-label="Target hours" onChange={(e) => setHours(Number(e.target.value))} />
          <span className="text-muted-foreground">h</span>
        </div>
      </td>
      <td className="px-2 py-2">
        <div className="flex w-28 items-center gap-1.5">
          <input type="number" className={INPUT} min={1} max={99} value={risk} disabled={!isAdmin}
            aria-label="At-risk probability (percent)" onChange={(e) => setRisk(Number(e.target.value))} />
          <span className="text-muted-foreground">%</span>
        </div>
      </td>
      <td className="px-2 py-2">
        <input type="checkbox" checked={alerts} disabled={!isAdmin} aria-label="Alert when at risk" onChange={(e) => setAlerts(e.target.checked)} />
      </td>
      <td className="px-2 py-2 text-xs text-muted-foreground">{policy.is_default ? "default" : formatTimestamp(policy.updated_at)}</td>
      <td className="px-2 py-2">
        {isAdmin ? (
          <button type="button" className={BUTTON_SECONDARY} disabled={save.isPending || !valid} onClick={() => save.mutate()}>Save</button>
        ) : null}
      </td>
    </tr>
  );
};

export const SlaPanel: React.FC<{
  readonly workspaceId: string;
  readonly isAdmin: boolean;
  readonly onOpenObject: (objectType: ObjectType, objectId: string) => void;
}> = ({ workspaceId, isAdmin, onOpenObject }) => {
  const sla = useQuery({ queryKey: processKeys.sla(workspaceId), queryFn: () => getSla(workspaceId), enabled: Boolean(workspaceId), refetchInterval: 60_000 });
  if (sla.isLoading) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (sla.isError || !sla.data) {
    return <p className="text-sm text-destructive">{errorMessage(sla.error, "Could not load service levels.")}</p>;
  }
  return (
    <div className="space-y-4">
      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="sla-policies">
        <h2 id="sla-policies" className={SECTION_TITLE}>Service levels</h2>
        <p className={HINT}>
          An object is breached when it is still open after its target. It is at risk when the model&apos;s probability of a
          breach reaches the threshold; an automation can listen for “SLA breach predicted”.
        </p>
        <div className={SCROLL_X}>
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className={TABLE_HEAD}>
                <th className="px-2 py-2">Object</th><th className="px-2 py-2">Target</th><th className="px-2 py-2">At risk from</th>
                <th className="px-2 py-2">Alert</th><th className="px-2 py-2">Set</th><th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {sla.data.policies.map((p) => <PolicyRow key={`${p.object_type}-${p.updated_at ?? "d"}`} workspaceId={workspaceId} policy={p} isAdmin={isAdmin} />)}
            </tbody>
          </table>
        </div>
      </section>
      <section className={`${SURFACE} p-4`} aria-labelledby="sla-models">
        <h2 id="sla-models" className={SECTION_TITLE}>The breach models</h2>
        <div className="mt-2 grid gap-4 md:grid-cols-3">
          {Object.entries(sla.data.runs).map(([type, run]) => <ModelRunCard key={type} objectType={type} run={run} />)}
        </div>
      </section>
      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="sla-predictions">
        <h2 id="sla-predictions" className={SECTION_TITLE}>Open objects</h2>
        {sla.data.predictions.length === 0 ? <p className={HINT}>No predictions: either nothing is open, or no model beat the base rate yet.</p> : (
          <div className={SCROLL_X}>
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className={TABLE_HEAD}>
                  <th className="px-2 py-2">Object</th><th className="px-2 py-2">Kind</th><th className="px-2 py-2">Due</th>
                  <th className="px-2 py-2">Breach probability</th><th className="px-2 py-2">State</th>
                </tr>
              </thead>
              <tbody>
                {sla.data.predictions.map((p) => (
                  <tr key={`${p.object_type}-${p.object_id}`} className={TABLE_ROW}>
                    <td className="px-2 py-2">
                      <button type="button" className="text-left font-semibold text-primary hover:underline"
                        onClick={() => onOpenObject(p.object_type as ObjectType, p.object_id)}>
                        {OBJECT_TYPE_LABELS[p.object_type as ObjectType] ?? p.object_type} · {p.object_id.slice(0, 8)}
                      </button>
                    </td>
                    <td className="px-2 py-2 text-xs">{p.kind ?? "—"}</td>
                    <td className="px-2 py-2 text-xs">{formatTimestamp(p.due_at)}</td>
                    <td className="px-2 py-2 tabular-nums">{pct(p.probability)}</td>
                    <td className="px-2 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATE_TONES[p.state] ?? ""}`}>{PREDICTION_STATE_LABELS[p.state] ?? p.state}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className={`${FIELD_LABEL} normal-case`}>Predictions are refreshed on every sweep (every 15 minutes).</p>
      </section>
    </div>
  );
};

export default SlaPanel;
