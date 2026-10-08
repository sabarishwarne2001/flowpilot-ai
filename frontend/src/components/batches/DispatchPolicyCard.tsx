/**
 * Phase 1 — the workspace's dispatch policy: where straight-through starts, where exceptions
 * start, and whether a missing required field holds a document for review. Admins change it.
 */
import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, SlidersHorizontal } from "lucide-react";

import { batchKeys, getDispatchPolicy, saveDispatchPolicy } from "@/services/api/batches";
import { errorMessage } from "@/services/api/errors";
import { LaneChip } from "@/components/batches/shared";

const toPercent = (value: number): number => Math.round(value * 100);

export const DispatchPolicyCard: React.FC<{ readonly workspaceId: string; readonly canEdit: boolean }> = ({
  workspaceId,
  canEdit,
}) => {
  const client = useQueryClient();
  const policy = useQuery({ queryKey: batchKeys.policy(workspaceId), queryFn: () => getDispatchPolicy(workspaceId) });
  const [straight, setStraight] = useState(90);
  const [floor, setFloor] = useState(60);
  const [requireFields, setRequireFields] = useState(true);
  const [tag, setTag] = useState(true);

  useEffect(() => {
    if (policy.data) {
      setStraight(toPercent(policy.data.straight_through_min_confidence));
      setFloor(toPercent(policy.data.review_min_confidence));
      setRequireFields(policy.data.require_required_fields);
      setTag(policy.data.tag_documents);
    }
  }, [policy.data]);

  const save = useMutation({
    mutationFn: () =>
      saveDispatchPolicy(workspaceId, {
        straight_through_min_confidence: straight / 100,
        review_min_confidence: floor / 100,
        require_required_fields: requireFields,
        tag_documents: tag,
      }),
    onSuccess: async () => {
      toast.success("Dispatch policy saved. It applies the next time a batch is dispatched.");
      await client.invalidateQueries({ queryKey: batchKeys.all(workspaceId) });
    },
    onError: (error) => toast.error(errorMessage(error, "The policy could not be saved.")),
  });

  const invalid = floor > straight;
  const dirty =
    policy.data !== undefined &&
    (straight !== toPercent(policy.data.straight_through_min_confidence) ||
      floor !== toPercent(policy.data.review_min_confidence) ||
      requireFields !== policy.data.require_required_fields ||
      tag !== policy.data.tag_documents);

  return (
    <section className="fp-card space-y-4 p-4" aria-labelledby="policy-title" data-testid="dispatch-policy">
      <header className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60 text-muted-foreground">
          <SlidersHorizontal className="h-4 w-4" aria-hidden />
        </span>
        <div>
          <h2 id="policy-title" className="text-[15px] font-semibold tracking-tight">Dispatch policy</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            Verified documents at or above the straight-through threshold, with every required field, go straight
            through. Below the floor they are exceptions. Unverified documents always go to review.
          </p>
        </div>
      </header>
      {policy.isLoading ? (
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="space-y-1.5">
              <span className="flex items-center justify-between text-xs font-medium">
                <span className="flex items-center gap-1.5"><LaneChip lane="STRAIGHT_THROUGH" /> from</span>
                <span className="fp-num text-sm font-semibold">{straight}%</span>
              </span>
              <input
                type="range" min={50} max={100} step={1} value={straight} disabled={!canEdit}
                onChange={(e) => setStraight(Number(e.target.value))}
                aria-label="Straight-through threshold (percent)"
                className="w-full accent-emerald-600 disabled:opacity-60"
              />
            </label>
            <label className="space-y-1.5">
              <span className="flex items-center justify-between text-xs font-medium">
                <span className="flex items-center gap-1.5"><LaneChip lane="EXCEPTION" /> below</span>
                <span className="fp-num text-sm font-semibold">{floor}%</span>
              </span>
              <input
                type="range" min={0} max={100} step={1} value={floor} disabled={!canEdit}
                onChange={(e) => setFloor(Number(e.target.value))}
                aria-label="Exception floor (percent)"
                className="w-full accent-red-600 disabled:opacity-60"
              />
            </label>
          </div>
          {invalid ? (
            <p role="alert" className="text-xs text-destructive">The exception floor cannot be above the straight-through threshold.</p>
          ) : null}
          <div className="space-y-2 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" className="h-4 w-4 accent-primary" checked={requireFields} disabled={!canEdit}
                     onChange={(e) => setRequireFields(e.target.checked)} />
              A missing required field holds the document for review
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" className="h-4 w-4 accent-primary" checked={tag} disabled={!canEdit}
                     onChange={(e) => setTag(e.target.checked)} />
              Tag dispatched documents (dispatch-straight-through, dispatch-review, dispatch-exception)
            </label>
          </div>
          {canEdit ? (
            <div className="flex items-center justify-end gap-2">
              {policy.data?.is_default ? <span className="text-xs text-muted-foreground">Using the defaults</span> : null}
              <button
                type="button"
                className="fp-btn fp-btn-primary h-8 text-xs"
                disabled={!dirty || invalid || save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                Save policy
              </button>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">Workspace admins can change the policy.</p>
          )}
        </>
      )}
    </section>
  );
};

export default DispatchPolicyCard;
