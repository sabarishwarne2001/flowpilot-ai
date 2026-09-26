/**
 * ARCH47-S2:document-postings — the "ERP postings" tab on Work Item details:
 * every posting built from an approved outcome this document belongs to, with
 * its state and the ERP's reference. Locked without capability.erp_posting.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { BookUp, Loader2 } from "lucide-react";

import { ErpLocked } from "@/components/erp/common";
import { PostingTable } from "@/components/erp/PostingTable";
import { CAPABILITY } from "@/constants/capabilities";
import { HINT } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { erpPath } from "@/routes/tenantPaths";
import { documentPostings, erpKeys } from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";

export const DocumentPostings: React.FC<{ readonly workItemId: string }> = ({ workItemId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.erpPosting);
  const query = useQuery({
    queryKey: erpKeys.document(workspaceId, workItemId),
    queryFn: () => documentPostings(workspaceId, workItemId),
    enabled: Boolean(workspaceId && workItemId && capability.granted),
  });
  if (!capability.granted) {
    return <ErpLocked compact />;
  }
  return (
    <section className="space-y-3" aria-labelledby="document-postings-title">
      <div className="flex flex-wrap items-center gap-2">
        <BookUp className="h-5 w-5 text-muted-foreground" aria-hidden />
        <h2 id="document-postings-title" className="text-lg font-bold">ERP postings</h2>
        <Link to={erpPath(orgSlug, workspaceSlug)} className="text-xs font-semibold text-primary hover:underline">All postings</Link>
      </div>
      <p className={HINT}>
        A document is posted once it is part of an approved outcome — an approved three-way match, an accepted table or a
        completed case. Each object is posted to each target exactly once.
      </p>
      {query.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "Could not load postings.")}</p> : null}
      {query.data ? <PostingTable rows={query.data.items} /> : null}
    </section>
  );
};

export default DocumentPostings;
