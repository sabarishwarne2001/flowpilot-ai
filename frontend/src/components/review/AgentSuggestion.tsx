/**
 * ARCH49-S2:agent-suggestion — the exception agent's proposal for one review
 * item, inside the review hub's expanded panel (Enterprise, contributors).
 *
 * It shows what the agent would decide and why, and lets the reviewer approve
 * it (applied through the same resolution path, with the version the agent
 * read), reject it with a reason, or undo a scheduled automatic resolution.
 * The full evidence — every tool call and the fenced excerpts — opens in
 * Process intelligence. It updates live: a `proposal.changed` event on the
 * hub's channel refetches it.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ProposalActions, ProposalSummary } from "@/components/process/ProposalPanel";
import { HINT } from "@/components/ui/primitives";
import { processProposalPath } from "@/routes/tenantPaths";
import { listProposals, processKeys } from "@/services/api/process";
import type { ReviewItem } from "@/types/review";

export const AgentSuggestion: React.FC<{
  readonly workspaceId: string;
  readonly item: ReviewItem;
  readonly canAct: boolean;
  readonly onApplied?: () => void;
}> = ({ workspaceId, item, canAct, onApplied }) => {
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const q = useQuery({
    queryKey: processKeys.forItem(workspaceId, item.item_id),
    queryFn: () => listProposals(workspaceId, { subject_id: item.item_id, subject_kind: item.kind, limit: 1 }),
    enabled: Boolean(workspaceId && canAct),
    staleTime: 15_000,
  });
  const proposal = q.data?.items[0];
  if (!canAct || !proposal) {
    return null;
  }
  const live = proposal.status === "PROPOSED" || proposal.status === "AUTO_SCHEDULED";
  if (!live && item.status === "OPEN") {
    return null;
  }
  return (
    <section className="mb-3 space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3" aria-label="The exception agent's proposal">
      <ProposalSummary proposal={proposal} />
      <div className="flex flex-wrap items-center gap-3">
        <ProposalActions workspaceId={workspaceId} proposal={proposal} canAct={canAct && item.status === "OPEN"} onDone={onApplied} />
        <Link className="text-xs font-semibold text-primary hover:underline" to={processProposalPath(orgSlug, workspaceSlug, proposal.id)}>
          What it read, and the source text
        </Link>
      </div>
      {item.status === "OPEN" && item.version !== proposal.subject_version ? (
        <p className={HINT}>The item changed after the agent read it; approving will be refused and a fresh proposal made on the next sweep.</p>
      ) : null}
    </section>
  );
};

export default AgentSuggestion;
