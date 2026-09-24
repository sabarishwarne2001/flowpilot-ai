/** ARCH43-S2:page-packet-splits — every split plan in the workspace, newest first. */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2, Scissors } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { HINT, PAGE_TITLE, SCROLL_X, SURFACE, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { packetSplitPath } from "@/routes/tenantPaths";
import { caseKeys } from "@/services/api/cases";
import { errorMessage } from "@/services/api/errors";
import { listPacketSplits } from "@/services/api/packets";

const PacketSplits: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.caseIntelligence);
  const query = useQuery({
    queryKey: caseKeys.splits(workspaceId),
    queryFn: () => listPacketSplits(workspaceId),
    enabled: Boolean(workspaceId && capability.granted),
  });
  if (!capability.granted) {
    return <p className={`${SURFACE} p-6 text-sm`}>The packet dicer is included on the Business and Enterprise plans.</p>;
  }
  return (
    <div className="space-y-4 p-4">
      <header className="flex items-center gap-2">
        <Scissors className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>Scanned packets</h1>
      </header>
      <p className={HINT}>Multi-page PDFs are checked for document boundaries after processing. Review a plan to divide the packet.</p>
      {query.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "Something went wrong.")}</p> : null}
      {query.data ? (
        <div className={`${SURFACE} ${SCROLL_X}`}>
          <table className="w-full text-sm">
            <thead>
              <tr className={TABLE_HEAD}>
                <th className="p-2 text-left">Packet</th><th className="p-2">Pages</th><th className="p-2">Documents</th>
                <th className="p-2">Status</th><th className="p-2">Certainty</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((row) => (
                <tr key={row.id} className={TABLE_ROW}>
                  <td className="p-2"><Link className="underline" to={packetSplitPath(orgSlug, workspaceSlug, row.id)}>{row.original_filename}</Link></td>
                  <td className="p-2 text-center">{row.page_count}</td>
                  <td className="p-2 text-center">{row.segment_count}</td>
                  <td className="p-2 text-center">{row.status}</td>
                  <td className="p-2 text-center">{row.certainty !== null ? `${Math.round(row.certainty * 100)}%` : "—"}</td>
                </tr>
              ))}
              {query.data.items.length === 0 ? (
                <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">No packets yet.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
};

export default PacketSplits;
