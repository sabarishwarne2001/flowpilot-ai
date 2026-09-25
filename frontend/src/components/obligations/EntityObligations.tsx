/**
 * ARCH46-S2:entity-obligations — the Obligations section of Entity 360: every
 * obligation tied to this record or to any record merged into it (obligations
 * hang off the record that was the root when they were read, and a later merge
 * must not orphan them). Locked without capability.obligations.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader2, Lock } from "lucide-react";

import ObligationTable from "@/components/obligations/ObligationTable";
import { CAPABILITY } from "@/constants/capabilities";
import { HINT } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { obligationsPath } from "@/routes/tenantPaths";
import { getEntityObligations, obligationKeys } from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";

interface Props {
  readonly entityId: string;
  readonly orgSlug: string;
  readonly workspaceSlug: string;
}

export const EntityObligations: React.FC<Props> = ({ entityId, orgSlug, workspaceSlug }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.obligations);
  const query = useQuery({
    queryKey: obligationKeys.entity(workspaceId, entityId),
    queryFn: () => getEntityObligations(workspaceId, entityId),
    enabled: Boolean(workspaceId && entityId && capability.granted),
  });
  if (!capability.granted) {
    return (
      <p className={`${HINT} flex items-center gap-2`}>
        <Lock className="h-3.5 w-3.5" aria-hidden />
        Renewals, notice periods and other obligations tied to this record are included on the Business and Enterprise plans.
      </p>
    );
  }
  if (query.isLoading) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (query.isError) {
    return <p className="text-sm text-destructive">{errorMessage(query.error, "Obligations could not be loaded.")}</p>;
  }
  const rows = query.data?.items ?? [];
  return (
    <div className="space-y-2">
      <ObligationTable rows={rows} orgSlug={orgSlug} workspaceSlug={workspaceSlug} hideParty
        empty="No obligations are tied to this record yet." />
      {rows.length > 0 ? (
        <Link className="text-xs font-semibold text-primary hover:underline" to={`${obligationsPath(orgSlug, workspaceSlug)}?entity=${encodeURIComponent(entityId)}`}>
          Open in Obligations
        </Link>
      ) : null}
    </div>
  );
};

export default EntityObligations;
