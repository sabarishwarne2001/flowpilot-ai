/**
 * ARCH45-S2:document-comparisons — the comparisons one document takes part in,
 * on Work Item details: each links to its matrix, marked out of date when a
 * document has been reprocessed since, and "Compare with…" opens a new
 * comparison with this document as the baseline. Renders nothing without
 * capability.universal_corroborator.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { GitCompare } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { HINT, SURFACE_INSET } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { corroborationPath, corroborationsPath } from "@/routes/tenantPaths";
import { corroborationKeys, getDocumentComparisons } from "@/services/api/corroboration";
import { STATUS_LABELS, STATUS_TONE } from "@/types/corroboration";

export const DocumentComparisons: React.FC<{ readonly workItemId: string }> = ({ workItemId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.universalCorroborator);
  const canCompare = workspace?.role === "ADMIN" || workspace?.role === "OWNER" || workspace?.role === "CONTRIBUTOR";
  const query = useQuery({
    queryKey: corroborationKeys.document(workspaceId, workItemId),
    queryFn: () => getDocumentComparisons(workspaceId, workItemId),
    enabled: Boolean(workspaceId && workItemId && capability.granted),
  });
  if (!capability.granted || !query.data) {
    return null;
  }
  const data = query.data;
  const compareWith = `${corroborationsPath(orgSlug, workspaceSlug)}?with=${encodeURIComponent(workItemId)}`;
  return (
    <section className={`${SURFACE_INSET} space-y-2 p-3`} aria-labelledby="document-comparisons-title">
      <div className="flex flex-wrap items-center gap-2">
        <GitCompare className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h3 id="document-comparisons-title" className="text-sm font-semibold">Comparisons</h3>
        {canCompare ? (
          <Link to={compareWith} className="ml-auto text-xs font-semibold text-primary hover:underline">Compare with…</Link>
        ) : null}
      </div>
      {data.runs.length === 0 ? (
        <p className={HINT}>This document has not been compared with any other yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {data.runs.map((run) => {
            const others = run.documents.filter((d) => d.work_item_id !== workItemId).map((d) => d.label);
            return (
              <li key={run.id} className="flex flex-wrap items-center gap-2">
                <Link className="underline" to={corroborationPath(orgSlug, workspaceSlug, run.id)}>
                  With {others.slice(0, 2).join(", ")}{others.length > 2 ? ` and ${others.length - 2} more` : ""}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {run.status === "COMPLETED" || run.status === "STALE"
                    ? `${run.material_count} material of ${run.discrepancy_count}`
                    : ""}
                </span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_TONE[run.status]}`}>
                  {STATUS_LABELS[run.status]}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

export default DocumentComparisons;
