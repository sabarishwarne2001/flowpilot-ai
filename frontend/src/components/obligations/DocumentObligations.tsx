/**
 * ARCH46-S2:document-obligations — the Obligations tab on Work Item details:
 * every renewal, notice deadline, payment, delivery, expiry and report read
 * from this document, with its due date in the workspace's zone, and "Read
 * again" (contributors) to re-run the deterministic extraction after an edit.
 * Locked without capability.obligations.
 */
import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { CalendarClock, Loader2, Lock, RefreshCw } from "lucide-react";

import ObligationTable from "@/components/obligations/ObligationTable";
import { CAPABILITY } from "@/constants/capabilities";
import { BUTTON_GHOST, HINT, SURFACE_INSET } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { obligationsPath } from "@/routes/tenantPaths";
import { extractDocumentObligations, getDocumentObligations, obligationKeys } from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";

export const DocumentObligations: React.FC<{ readonly workItemId: string }> = ({ workItemId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.obligations);
  const canEdit = workspace?.role === "ADMIN" || workspace?.role === "OWNER" || workspace?.role === "CONTRIBUTOR";
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: obligationKeys.document(workspaceId, workItemId),
    queryFn: () => getDocumentObligations(workspaceId, workItemId),
    enabled: Boolean(workspaceId && workItemId && capability.granted),
  });
  const again = useMutation({
    mutationFn: () => extractDocumentObligations(workspaceId, workItemId),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: obligationKeys.all(workspaceId) }); },
  });

  if (!capability.granted) {
    return (
      <section className={`${SURFACE_INSET} flex items-center gap-2 p-3 text-sm`} aria-label="Obligations">
        <Lock className="h-4 w-4" aria-hidden />
        <span>Renewals, notice deadlines and payments read from documents are included on the Business and Enterprise plans.</span>
      </section>
    );
  }
  return (
    <section className="space-y-3" aria-labelledby="document-obligations-title">
      <div className="flex flex-wrap items-center gap-2">
        <CalendarClock className="h-5 w-5 text-muted-foreground" aria-hidden />
        <h2 id="document-obligations-title" className="text-lg font-bold">Obligations</h2>
        <Link to={obligationsPath(orgSlug, workspaceSlug)} className="text-xs font-semibold text-primary hover:underline">All obligations</Link>
        {canEdit ? (
          <button type="button" className={`${BUTTON_GHOST} ml-auto`} disabled={again.isPending} onClick={() => again.mutate()}>
            {again.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />}
            Read again
          </button>
        ) : null}
      </div>
      <p className={HINT}>
        Read deterministically from the document&apos;s clauses and fields — no model is called. Dates the document leaves in
        doubt wait for a person to confirm them in the review hub.
      </p>
      {again.data ? (
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {again.data.created} new, {again.data.kept} unchanged, {again.data.superseded} no longer in the document
          {again.data.pending ? `, ${again.data.pending} to confirm` : ""}.
        </p>
      ) : null}
      {again.isError ? <p className="text-sm text-destructive">{errorMessage(again.error, "The document could not be read again.")}</p> : null}
      {query.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "Obligations could not be loaded.")}</p> : null}
      {query.data ? (
        <ObligationTable rows={query.data.items} orgSlug={orgSlug} workspaceSlug={workspaceSlug} hideDocument
          empty="No obligations were read from this document." />
      ) : null}
    </section>
  );
};

export default DocumentObligations;
