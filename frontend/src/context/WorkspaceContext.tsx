/**
 * Workspace compatibility adapter for FlowPilot AI.
 *
 * TRANSITIONAL. This file is deleted at the end of ARCH-01 frontend Step 8b,
 * once every consumer reads from useResolvedTenant directly. It exists so
 * roughly ten components can be migrated one at a time under compiler
 * protection rather than all at once.
 *
 * WHAT IT REPLACES
 *
 * The previous implementation was a provider mounted above <App /> that
 * fetched unconditionally:
 *
 *   queryFn: () => token ? getWorkspace() : getPublicWorkspace()
 *
 * Both endpoints were deleted in the ARCH-01 backend transformation.
 * GET /workspace resolved "the user's workspace" from a single active
 * membership — an assumption that returned HTTP 500 for any account holding
 * two. GET /workspace/public returned the oldest workspace row in the database
 * to anonymous visitors, disclosing one tenant's name, logo, and locale to
 * everyone who reached the login screen.
 *
 * WHAT IT DOES NOW
 *
 * Reads the active workspace identifier from TenantContext and fetches the
 * full record from GET /workspaces/{workspace_id} — scoped, authorized, and
 * present in the current API. No provider is mounted; the data follows
 * TenantGuard.
 *
 * OUTSIDE TENANT SCOPE
 *
 * Returns workspace: null with no request. AuthLayout calls this on the login
 * screen, where no tenant exists and none should be inferred. Its footer falls
 * back to the product name, which is the correct behaviour — the previous
 * "public workspace" branding was the disclosure described above.
 */

import { useMemo, type ReactNode } from "react";

import { useQuery } from "@tanstack/react-query";

import { useOptionalTenant } from "@/routes/TenantContext";
import { getWorkspaceById } from "@/services/api/workspaces";
import type { Workspace as TenantWorkspace } from "@/types/tenancy";

/**
 * The shape legacy consumers expect.
 *
 * Extends the ARCH-01 Workspace record with two fields that no longer exist on
 * the model, so components reading them keep working until 8b migrates them:
 *
 *   company_name  moved to Organization.name in ARCH-01. It was always the
 *                 tenant's identity, not the workspace's. Projected from the
 *                 organization so the value is correct, not merely present.
 *
 *   is_active     replaced by the WorkspaceStatus enum, which distinguishes
 *                 archived from suspended where a boolean could not. Derived
 *                 here for compatibility; new code should read `status`.
 */
export interface LegacyWorkspaceView extends TenantWorkspace {
  company_name: string;
  is_active: boolean;
}

interface WorkspaceContextValue {
  workspace: LegacyWorkspaceView | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => Promise<unknown>;
}

/**
 * Retained as a no-op passthrough.
 *
 * The provider is gone, but removing the export in the same change as
 * unmounting it would break any file still importing the symbol. It renders
 * children unchanged and holds no state. Deleted with this file in 8b.
 */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

/**
 * Returns the active workspace, or null outside tenant scope.
 *
 * Keeps the original return shape so existing consumers compile unchanged.
 * New code should call useResolvedTenant, which exposes the organization, both
 * roles, and the full organization list without this projection.
 */
export function useWorkspace(): WorkspaceContextValue {
  const tenant = useOptionalTenant();

  const workspaceId = tenant?.workspace.id ?? null;
  const organizationName = tenant?.organization.organization_name ?? null;

  const query = useQuery({
    // Namespaced under "workspaces" so ARCH-01 invalidations reach it. The
    // previous key was ["workspace"], which no current mutation touches.
    queryKey: ["workspaces", "detail", workspaceId],
    queryFn: () => getWorkspaceById(workspaceId as string),
    enabled: workspaceId !== null,
    staleTime: 1000 * 60 * 5,
  });

  const workspace = useMemo<LegacyWorkspaceView | null>(() => {
    if (!query.data) {
      return null;
    }

    return {
      ...query.data,
      company_name: organizationName ?? query.data.workspace_name,
      is_active: query.data.status === "ACTIVE",
    };
  }, [query.data, organizationName]);

  return {
    workspace,
    // Never reports loading outside tenant scope: there is nothing to wait
    // for, and a permanent spinner in the login footer would be worse than the
    // fallback text.
    isLoading: workspaceId !== null && query.isLoading,
    error: (query.error as Error | null) ?? null,
    refetch: query.refetch,
  };
}
