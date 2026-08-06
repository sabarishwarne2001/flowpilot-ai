/**
 * Resolved tenant context for FlowPilot AI.
 *
 * TenantGuard publishes the tenant it resolved; every descendant reads it from
 * here. Two properties matter:
 *
 *   1. The value is NON-NULLABLE. A component beneath TenantGuard cannot be
 *      rendered without a resolved tenant, so it should never have to write
 *      `tenant?.workspace?.id`. Optional chaining on a value that is
 *      structurally guaranteed invites callers to handle a case that cannot
 *      occur, and to handle it wrongly.
 *
 *   2. Reading outside the provider THROWS rather than returning null. A
 *      component that needs tenant context and is mounted outside the guard is
 *      a routing mistake, and it should fail immediately and loudly rather
 *      than rendering an empty state that looks like "no data".
 *
 * Distinct from the legacy src/context/WorkspaceContext.tsx, which fetches its
 * own workspace via the deleted GET /workspace endpoint. That file is removed
 * in Step 8, once its consumers read from here.
 */

import React, { createContext, useContext, useMemo } from "react";

import type {
  MeUser,
  OrganizationMembershipSummary,
  OrganizationRole,
  WorkspaceRole,
  WorkspaceSummary,
} from "@/types/tenancy";

export interface ResolvedTenant {
  user: MeUser;
  organization: OrganizationMembershipSummary;
  workspace: WorkspaceSummary;
  /** The actor's role in this organization. */
  organizationRole: OrganizationRole;
  /**
   * The actor's EFFECTIVE role in this workspace.
   *
   * Already includes derived elevation: an organization OWNER or ADMIN reads
   * ADMIN here whether or not a stored grant exists. Never compare against a
   * raw membership role — that is the mistake the pre-ARCH-01 inline checks
   * made, and it hid every control from the most privileged users.
   */
  workspaceRole: WorkspaceRole;
  /** Every organization the actor belongs to. Backs the switcher. */
  organizations: OrganizationMembershipSummary[];
}

const TenantContext = createContext<ResolvedTenant | null>(null);

interface TenantContextProviderProps {
  value: ResolvedTenant;
  children: React.ReactNode;
}

export const TenantContextProvider: React.FC<TenantContextProviderProps> = ({
  value,
  children,
}) => {
  // Memoised on the identifiers rather than the object, so a background
  // refetch that returns structurally equal data does not re-render every
  // tenant-scoped component in the tree.
  const memoised = useMemo(
    () => value,
    [
      value.user.id,
      value.organization.organization_id,
      value.workspace.id,
      value.organizationRole,
      value.workspaceRole,
      value.organizations,
      value,
    ],
  );

  return (
    <TenantContext.Provider value={memoised}>{children}</TenantContext.Provider>
  );
};

/**
 * Reads the resolved tenant.
 *
 * Throws when called outside TenantGuard. That is deliberate: a tenant-scoped
 * component mounted on a non-tenant route is a routing bug, and returning null
 * would let it render an empty state that looks like legitimately absent data.
 */
export const useResolvedTenant = (): ResolvedTenant => {
  const context = useContext(TenantContext);

  if (context === null) {
    throw new Error(
      "useResolvedTenant must be used within TenantGuard. A component that " +
        "needs tenant context is mounted on a route that does not provide " +
        "one — check the route tree in App.tsx.",
    );
  }

  return context;
};

/**
 * Reads the resolved tenant, or null outside the provider.
 *
 * For components rendered on both tenant and non-tenant routes — a header that
 * shows a workspace name when one exists, for instance. Prefer
 * useResolvedTenant everywhere else: an unnecessary null check is an
 * unnecessary branch, and unnecessary branches accumulate.
 */
export const useOptionalTenant = (): ResolvedTenant | null =>
  useContext(TenantContext);
