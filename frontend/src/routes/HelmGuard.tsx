import React from "react";
import { Link, Outlet } from "react-router-dom";
import { Compass } from "lucide-react";

import { seesHelm } from "@/components/helm/modeMemory";
import { useTenant } from "@/hooks/useTenant";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { verificationPath, workspaceDashboardPath } from "@/routes/tenantPaths";

/**
 * Helm is for organization owners, admins and the billing role (brief §5.4). Mounted under
 * OrganizationGuard, which has already resolved the organization from the address. A member who
 * opens Helm's address is told where their Helm work is (the review hub), not sent elsewhere
 * silently; the API refuses them anyway (403 HELM_ROLE).
 */
export const HelmGuard: React.FC = () => {
  const { organization, organizationRole } = useResolvedOrganization();
  const { state } = useTenant();

  if (seesHelm(organizationRole)) {
    return <Outlet />;
  }

  const workspace = state.status === "ready" ? state.workspace : null;
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
      <div className="fp-card max-w-md p-6 text-center">
        <Compass className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden="true" />
        <h1 className="mt-3 text-lg font-semibold">Helm is for owners, admins and billing</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Helm plans payments and checks every action for {organization.organization_name}. Tasks it asks you to do
          (verifying a bank account, deciding a renewal) are in your review hub.
        </p>
        {workspace ? (
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            <Link
              to={verificationPath(organization.organization_slug, workspace.slug)}
              className="fp-btn fp-btn-primary"
            >
              Open the review hub
            </Link>
            <Link to={workspaceDashboardPath(organization.organization_slug, workspace.slug)} className="fp-btn fp-btn-secondary">
              Back to Documents
            </Link>
          </div>
        ) : null}
      </div>
    </main>
  );
};

export default HelmGuard;
