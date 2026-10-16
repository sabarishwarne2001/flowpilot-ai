import React from "react";
import { Link } from "react-router-dom";
import { Lock } from "lucide-react";

import AskPlanOwners from "@/components/billing/AskPlanOwners";
import { useHelm } from "@/components/helm/HelmContext";
import { organizationBillingPath } from "@/routes/tenantPaths";

/**
 * What a part of Helm on the Enterprise plan says on a smaller one: owners and the billing role get
 * "See plans" (the Preview banner's pattern); everyone else is told whom to ask, by name
 * (`AskPlanOwners`). No price and no button that changes anything: a plan change is the only route.
 */
export const EnterpriseNote: React.FC<{
  /** The start of the sentence: "Custom conditions are", "This mandate is". */
  readonly what: string;
  readonly className?: string;
}> = ({ what, className = "" }) => {
  const { organizationId, orgSlug, role } = useHelm();
  return (
    <p className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${className}`} data-testid="helm-upgrade-note">
      <Lock className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{what} part of the Enterprise plan.</span>
      {role === "OWNER" || role === "BILLING" ? (
        <Link to={organizationBillingPath(orgSlug)} className="helm-accent hover:underline">
          See plans
        </Link>
      ) : (
        <AskPlanOwners organizationId={organizationId} className="helm-muted" />
      )}
    </p>
  );
};

export default EnterpriseNote;
