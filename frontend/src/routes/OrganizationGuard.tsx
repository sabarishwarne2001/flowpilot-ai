import React, { createContext, useContext, useEffect, useMemo } from "react";
import { Navigate, Outlet, useLocation, useParams } from "react-router-dom";
import { toast } from "sonner";

import { LoadingScreen } from "@/components/common/LoadingScreen";
import { useTenant } from "@/hooks/useTenant";
import { ROUTES } from "@/constants/routes";
import { organizationSettingsPath } from "@/routes/tenantPaths";
import type { OrganizationMembershipSummary } from "@/types/tenancy";

export interface ResolvedOrganization {
  readonly organization: OrganizationMembershipSummary;
  readonly organizationId: string;
  readonly organizationRole: string;
}

const OrganizationContext = createContext<ResolvedOrganization | null>(null);

export const useResolvedOrganization = (): ResolvedOrganization => {
  const value = useContext(OrganizationContext);
  if (!value) {
    throw new Error(
      "useResolvedOrganization must be used within an OrganizationGuard. Mount this page under ROUTE_PATTERNS.organizationShell.",
    );
  }
  return value;
};

export const OrganizationGuard: React.FC = () => {
  const { orgSlug } = useParams<{ orgSlug: string }>();
  const { state } = useTenant();
  const location = useLocation();

  const organizations = useMemo<OrganizationMembershipSummary[]>(
    () =>
      state.status === "ready" || state.status === "no_workspace"
        ? state.organizations
        : [],
    [state],
  );

  const resolved = useMemo<ResolvedOrganization | null>(() => {
    const organization = organizations.find(
      (candidate) => candidate.organization_slug === orgSlug,
    );
    if (!organization) {
      return null;
    }
    return {
      organization,
      organizationId: organization.organization_id,
      organizationRole: String(organization.role),
    };
  }, [organizations, orgSlug]);

  if (state.status === "loading") {
    return <LoadingScreen />;
  }

  if (state.status === "unauthenticated") {
    return <Navigate to={ROUTES.LOGIN} replace />;
  }

  if (state.status === "onboarding_required") {
    return <Navigate to={ROUTES.ONBOARDING} replace />;
  }

  if (!resolved) {
    return <Navigate to={ROUTES.WORKSPACES} replace />;
  }

  return (
    <ArchivedOrganizationGate
      resolved={resolved}
      pathname={location.pathname}
    />
  );
};

/**
 * Narrows an archived organization to its General page.
 *
 * An archived organization is readable, not operable. Its records survive on
 * purpose -- that is the entire argument for archiving rather than deleting --
 * so General must stay reachable to show the status and the retention notice.
 * Every other console under this shell (Billing, Members, BYOK, Analytics,
 * Compliance) issues writes or queries that `deps.OrgContext` refuses, and a
 * page whose every request 403s is a worse answer than being told why.
 *
 * A separate component because the toast is a side effect and side effects
 * belong in an effect. Firing it inline in a render path means React
 * StrictMode's double-invoke shows it twice in development -- a real bug that
 * reads as a rendering glitch and gets "fixed" by disabling StrictMode.
 */
const ArchivedOrganizationGate: React.FC<{
  resolved: ResolvedOrganization;
  pathname: string;
}> = ({ resolved, pathname }) => {
  const isArchived = resolved.organization.organization_status !== "ACTIVE";
  const generalPath = organizationSettingsPath(
    resolved.organization.organization_slug,
  );
  const isOnGeneral = pathname === generalPath;
  const shouldRedirect = isArchived && !isOnGeneral;

  useEffect(() => {
    if (shouldRedirect) {
      toast.info("This organization is archived. Records are read-only.");
    }
  }, [shouldRedirect]);

  if (shouldRedirect) {
    return <Navigate to={generalPath} replace />;
  }

  return (
    <OrganizationContext.Provider value={resolved}>
      <Outlet />
    </OrganizationContext.Provider>
  );
};

export default OrganizationGuard;
