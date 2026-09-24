/**
 * ARCH42-S2:entity-chips — the records a document names, on Work Item details.
 * Renders nothing without capability.entity_graph.
 */
import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2, Network } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { HINT, SURFACE_INSET } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { entityPath } from "@/routes/tenantPaths";
import { entityKeys, getWorkItemEntities, resolveWorkItemEntities } from "@/services/api/entities";

const DECISION_TONE: Readonly<Record<string, string>> = {
  REVIEW: "border-amber-500/50 bg-amber-500/10",
  REJECTED: "border-border bg-muted line-through",
};

export const EntityChips: React.FC<{ readonly workItemId: string }> = ({ workItemId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.entityGraph);
  const queryClient = useQueryClient();
  const canResolve = workspace?.role === "ADMIN" || workspace?.role === "OWNER" || workspace?.role === "CONTRIBUTOR";
  const query = useQuery({
    queryKey: entityKeys.workItem(workspaceId, workItemId),
    queryFn: () => getWorkItemEntities(workspaceId, workItemId),
    enabled: Boolean(workspaceId && workItemId && capability.granted),
  });
  const resolve = useMutation({
    mutationFn: () => resolveWorkItemEntities(workspaceId, workItemId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: entityKeys.all(workspaceId) }),
  });
  if (!capability.granted || !query.data) {
    return null;
  }
  return (
    <section className={`${SURFACE_INSET} space-y-2 p-3`} aria-labelledby="entity-chips-title">
      <div className="flex items-center gap-2">
        <Network className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h3 id="entity-chips-title" className="text-sm font-semibold">
          Records in this document
        </h3>
        {canResolve && (
          <button
            type="button"
            onClick={() => resolve.mutate()}
            disabled={resolve.isPending}
            className="ml-auto text-xs font-semibold text-primary hover:underline disabled:opacity-50"
          >
            {resolve.isPending ? <Loader2 className="inline h-3 w-3 animate-spin" /> : "Re-resolve"}
          </button>
        )}
      </div>
      {query.data.chips.length === 0 ? (
        <p className={HINT}>No people, organizations or references were found in this document's fields.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {query.data.chips.map((chip) => (
            <li key={`${chip.field_path}-${chip.entity_id}`}>
              <Link
                to={entityPath(orgSlug, workspaceSlug, chip.entity_id)}
                title={`${chip.role.replace(/_/g, " ")} · ${chip.kind.toLowerCase()}${chip.decision === "REVIEW" ? " · awaiting review" : ""}`}
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs hover:bg-muted ${DECISION_TONE[chip.decision] ?? "border-border"}`}
              >
                <span className="font-medium">{chip.display_name}</span>
                <span className="text-muted-foreground">{chip.role.replace(/_/g, " ")}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default EntityChips;
