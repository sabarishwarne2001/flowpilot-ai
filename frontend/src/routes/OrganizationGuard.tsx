import React, { createContext, useContext, useMemo } from "react";
import { Navigate, Outlet, useParams } from "react-router-dom";

import { LoadingScreen } from "@/components/common/LoadingScreen";
import { useTenant } from "@/hooks/useTenant";
import { ROUTES } from "@/constants/routes";
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
    <OrganizationContext.Provider value={resolved}>
      <Outlet />
    </OrganizationContext.Provider>
  );
};

export default OrganizationGuard;
