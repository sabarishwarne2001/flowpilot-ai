/**
 * ARCH49-S2:agent-policy — how far the exception agent may go in this workspace.
 *
 * Planning (proposals for a person) is on by default. Automatic resolution is
 * off by default and can only ever cover the kinds whose decision ARCH-35
 * bounds (a conformal error limit the tenant set): even then each one is
 * scheduled, shown to the workspace's admins, applied only after the hold,
 * re-checked against the live calibration model first, and can be undone
 * until then. Switching it on is recorded against the admin who did it; the
 * agent applies as that person, and stops if they stop being an admin.
 */
import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { BUTTON_PRIMARY, FIELD_LABEL, HINT, INPUT, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";
import { getAgentPolicy, processKeys, setAgentPolicy } from "@/services/api/process";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import { PROPOSAL_KIND_LABELS, type AgentPolicyUpdate, type ProposalKind } from "@/types/process";

export const AgentPolicyPanel: React.FC<{ readonly workspaceId: string; readonly isAdmin: boolean }> = ({ workspaceId, isAdmin }) => {
  const queryClient = useQueryClient();
  const policy = useQuery({ queryKey: processKeys.policy(workspaceId), queryFn: () => getAgentPolicy(workspaceId), enabled: Boolean(workspaceId) });
  const [draft, setDraft] = useState<AgentPolicyUpdate | null>(null);
  useEffect(() => {
    if (policy.data) {
      setDraft({
        planning_enabled: policy.data.planning_enabled,
        auto_apply_enabled: policy.data.auto_apply_enabled,
        auto_apply_kinds: policy.data.auto_apply_kinds,
        hold_minutes: policy.data.hold_minutes,
      });
    }
  }, [policy.data]);
  const save = useMutation({
    mutationFn: (body: AgentPolicyUpdate) => setAgentPolicy(workspaceId, body),
    onSuccess: (out) => {
      queryClient.setQueryData(processKeys.policy(workspaceId), out);
      void queryClient.invalidateQueries({ queryKey: processKeys.overview(workspaceId) });
      toast.success("Saved.");
    },
    onError: (error) => toast.error(errorMessage(error, "Could not save the policy.")),
  });
  if (policy.isLoading || !draft) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (policy.isError || !policy.data) {
    return <p className="text-sm text-destructive">{errorMessage(policy.error, "Could not load the policy.")}</p>;
  }
  const toggleKind = (kind: string): void =>
    setDraft({
      ...draft,
      auto_apply_kinds: draft.auto_apply_kinds.includes(kind)
        ? draft.auto_apply_kinds.filter((k) => k !== kind)
        : [...draft.auto_apply_kinds, kind],
    });
  const holdValid = Number.isInteger(draft.hold_minutes) && draft.hold_minutes >= 5 && draft.hold_minutes <= 1440;
  return (
    <section className={`${SURFACE} space-y-4 p-4`} aria-labelledby="agent-policy">
      <h2 id="agent-policy" className={SECTION_TITLE}>The agent&apos;s policy</h2>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" disabled={!isAdmin} checked={draft.planning_enabled}
          onChange={(e) => setDraft({ ...draft, planning_enabled: e.target.checked })} />
        <span>
          <span className="font-semibold">Propose resolutions</span>
          <span className={`${HINT} block`}>The agent works the review queue and incomplete or inconsistent cases and proposes a decision for a person to approve.</span>
        </span>
      </label>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" disabled={!isAdmin} checked={draft.auto_apply_enabled}
          onChange={(e) => setDraft({ ...draft, auto_apply_enabled: e.target.checked })} />
        <span>
          <span className="font-semibold">Let bounded decisions apply themselves</span>
          <span className={`${HINT} block`}>
            Only decisions your calibration bounds (the error limit set under calibrated autonomy) can ever qualify. Each is
            scheduled, announced to admins, re-checked against the live model, and can be undone until the hold ends.
          </span>
        </span>
      </label>
      <fieldset className="space-y-1 pl-6" disabled={!isAdmin || !draft.auto_apply_enabled}>
        <legend className={FIELD_LABEL}>Which decisions</legend>
        {policy.data.auto_capable_kinds.map((kind) => (
          <label key={kind} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.auto_apply_kinds.includes(kind)} onChange={() => toggleKind(kind)} />
            {PROPOSAL_KIND_LABELS[kind as ProposalKind] ?? kind}
          </label>
        ))}
        <p className={HINT}>Every other kind — radar findings, merges, splits, tables, comparisons, postings, routing and cases — always waits for a person.</p>
        <label className="mt-2 flex items-center gap-2 text-sm">
          <span>Hold before applying</span>
          <input type="number" min={5} max={1440} className={`${INPUT} w-24`} value={draft.hold_minutes}
            onChange={(e) => setDraft({ ...draft, hold_minutes: Number(e.target.value) })} aria-invalid={!holdValid} />
          <span>minutes (5–1440)</span>
        </label>
      </fieldset>
      {policy.data.enabled_at ? (
        <p className={HINT}>Automatic resolution was switched on {formatTimestamp(policy.data.enabled_at)}; the agent applies as the admin who did it.</p>
      ) : null}
      {isAdmin ? (
        <button type="button" className={BUTTON_PRIMARY} disabled={save.isPending || !holdValid} onClick={() => save.mutate(draft)}>
          {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null} Save policy
        </button>
      ) : (
        <p className={HINT}>A workspace admin sets the policy.</p>
      )}
    </section>
  );
};

export default AgentPolicyPanel;
