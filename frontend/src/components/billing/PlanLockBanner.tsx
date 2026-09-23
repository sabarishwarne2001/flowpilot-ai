import React from "react";
import { Link } from "react-router-dom";
import { Lock } from "lucide-react";

import { joinPlanNames } from "@/components/billing/UpgradePlanDialog";
import type { CapabilityKey } from "@/constants/capabilities";
import { useGrantedCapabilities } from "@/hooks/useGrantedCapabilities";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { organizationBillingPath } from "@/routes/tenantPaths";

interface PlanLockBannerProps {
  readonly capability: CapabilityKey;
  readonly feature: string;
}

/**
 * HM-S1:plan-lock-banner — shown above an organization page whose writes the
 * plan does not include.
 *
 * A banner rather than a replacement card: reading, revoking and deleting are
 * never gated, so a tenant that downgraded can still see and remove what it
 * set up. The server refuses the writes (402 CAPABILITY_REQUIRED); this says
 * so before anyone fills in a form. Renders nothing while loading and when
 * the capability is held, so an entitled tenant never sees it flash.
 */
export const PlanLockBanner: React.FC<PlanLockBannerProps> = ({ capability, feature }) => {
  const { organizationId, organization, organizationRole } = useResolvedOrganization();
  const { granted, plansFor, isLoading, isError } = useGrantedCapabilities(organizationId);
  if (isLoading || isError || granted.has(capability)) {
    return null;
  }
  const role = String(organizationRole).toUpperCase();
  const canChangePlan = role === "OWNER" || role === "BILLING";
  return (
    <section
      role="status"
      data-testid="plan-lock-banner"
      className="mb-4 flex flex-wrap items-start gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4"
    >
      <Lock className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-foreground">{feature} isn&apos;t included in your plan</p>
        <p className="mt-0.5 text-sm text-muted-foreground">
          It&apos;s included on {joinPlanNames(plansFor(capability))}. Anything already set up stays
          visible and can be removed; creating or changing it needs a plan that includes {feature}.
        </p>
      </div>
      {canChangePlan ? (
        <Link
          to={organizationBillingPath(organization.organization_slug)}
          className="inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90"
        >
          View plans
        </Link>
      ) : (
        <p className="text-xs text-muted-foreground">Ask an organization owner to upgrade.</p>
      )}
    </section>
  );
};

export default PlanLockBanner;
