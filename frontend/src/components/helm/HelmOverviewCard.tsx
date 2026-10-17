import React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Compass, Lock } from "lucide-react";

import { seesHelm } from "@/components/helm/modeMemory";
import { helmPath } from "@/routes/tenantPaths";
import { getHelmWorkspaceCard, helmKeys } from "@/services/api/helm";
import { formatHelmMoney } from "@/components/helm/money";

/**
 * Helm on the workspace Overview (brief §10.3), in Documents mode's own look: what Helm has held
 * here and what waits for a decision; "start Helm here" on Business and up; the sample company on
 * other plans. Owners, admins and billing only, like Helm itself; members never see it.
 */
export const HelmOverviewCard: React.FC<{
  readonly organizationId: string;
  readonly orgSlug: string;
  readonly workspaceId: string;
  readonly workspaceName: string;
  readonly organizationRole: string | null | undefined;
}> = ({ organizationId, orgSlug, workspaceId, workspaceName, organizationRole }) => {
  const visible = seesHelm(organizationRole);
  const card = useQuery({
    queryKey: helmKeys.workspaceCard(organizationId, workspaceId),
    queryFn: () => getHelmWorkspaceCard(organizationId, workspaceId),
    enabled: visible,
    staleTime: 60_000,
    retry: false,
  });
  if (!visible || !card.data) {
    return null;
  }
  const { state, book_id: bookId, value_held: held, waiting } = card.data;
  const amounts = Object.entries(held).filter(([, micros]) => micros > 0);
  return (
    <section className="fp-card flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between" aria-labelledby="helm-overview-card" data-testid="helm-overview-card">
      <div className="flex min-w-0 items-start gap-3">
        <Compass className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
        <div className="min-w-0">
          <h2 id="helm-overview-card" className="text-[15px] font-semibold tracking-tight">
            Helm
            {state === "preview" ? (
              <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 align-middle text-[11px] font-medium text-muted-foreground">
                <Lock className="h-3 w-3" aria-hidden="true" />
                Business and Enterprise
              </span>
            ) : null}
          </h2>
          {state === "active" ? (
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              {amounts.length > 0 ? (
                <>
                  Value held here:{" "}
                  {amounts.map(([currency, micros], i) => (
                    <React.Fragment key={currency}>
                      {i > 0 ? ", " : null}
                      <span className="fp-num font-semibold text-foreground">{formatHelmMoney(micros, currency)}</span>
                    </React.Fragment>
                  ))}
                  , backed by evidence.
                </>
              ) : (
                "No value held yet: Helm counts only outcomes backed by evidence."
              )}{" "}
              {waiting > 0 ? (
                <>
                  <span className="fp-num font-semibold text-foreground">{waiting}</span> {waiting === 1 ? "action waits" : "actions wait"} for a decision.
                </>
              ) : (
                "Nothing waits for a decision."
              )}
            </p>
          ) : state === "available" ? (
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              Helm keeps the promises in {workspaceName}'s documents: suppliers paid on time and never twice, never above
              the agreed price, cash above your floor. It suggests; a person decides.
            </p>
          ) : (
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              See what Helm would do with a sample company, or replay three months of your own documents.
            </p>
          )}
        </div>
      </div>
      <Link
        to={
          state === "active" && bookId
            ? `${helmPath(orgSlug)}?book=${encodeURIComponent(bookId)}`
            : helmPath(orgSlug, "start")
        }
        className="inline-flex shrink-0 items-center justify-center rounded-md border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted"
      >
        {state === "active" ? (waiting > 0 ? "Review in Helm" : "Open Helm") : state === "available" ? "Start Helm here" : "Explore Helm"}
      </Link>
    </section>
  );
};

export default HelmOverviewCard;
