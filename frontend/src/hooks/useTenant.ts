/**
 * The single source of truth for tenant context in the FlowPilot AI UI.
 *
 * Composes the bootstrap query with the persisted selection and returns a
 * discriminated TenantState. Every guard, switcher, and tenant-scoped page
 * reads from here.
 *
 * Consumers switch on `state.status`. Because TenantState is a discriminated
 * union, a consumer that omits a case fails to compile — which is the
 * structural difference from the boolean it replaces. The old guard could not
 * distinguish an expired session from a new user, so it defaulted to the most
 * destructive branch and sent both to organization creation.
 */

import { useCallback, useEffect, useMemo } from "react";

import { useMeContext } from "@/hooks/useMeContext";
import { resolveTenant } from "@/hooks/tenantResolution";
import type { TenantState } from "@/hooks/tenantResolution";
import { useAuthStore } from "@/store/useAuthStore";
import { useTenantStore } from "@/store/useTenantStore";

export interface UseTenantResult {
  state: TenantState;
  /** True while the context is refetching in the background. */
  isRefreshing: boolean;
  refresh: () => void;
  /** Switches organization. Resolution picks the workspace. */
  selectOrganization: (organizationId: string) => void;
  selectWorkspace: (organizationId: string, workspaceId: string) => void;
}

export const useTenant = (): UseTenantResult => {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  const { context, isLoading, isFetching, isUnauthorized, error, refetch } =
    useMeContext();

  const activeOrganizationId = useTenantStore(
    (state) => state.activeOrganizationId,
  );
  const activeWorkspaceId = useTenantStore((state) => state.activeWorkspaceId);
  const lastWorkspaceByOrganization = useTenantStore(
    (state) => state.lastWorkspaceByOrganization,
  );
  const setActiveOrganization = useTenantStore(
    (state) => state.setActiveOrganization,
  );
  const setActiveWorkspace = useTenantStore(
    (state) => state.setActiveWorkspace,
  );

  const selection = useMemo(
    () => ({
      activeOrganizationId,
      activeWorkspaceId,
      lastWorkspaceByOrganization,
    }),
    [activeOrganizationId, activeWorkspaceId, lastWorkspaceByOrganization],
  );

  const state = useMemo(
    () =>
      resolveTenant({
        isAuthenticated,
        isLoading,
        isUnauthorized,
        error,
        context,
        selection,
      }),
    [isAuthenticated, isLoading, isUnauthorized, error, context, selection],
  );

  // Self-healing write-back.
  //
  // Resolution falls back silently when a persisted identifier no longer
  // resolves — a removed membership, an archived workspace. Persisting the
  // resolved values means the next boot starts from a valid selection rather
  // than repeating the fallback, and the switcher highlights what the user is
  // actually looking at.
  //
  // Guarded by an inequality check, so this writes only when the resolved
  // values genuinely differ from what is stored. Without the guard the store
  // update would retrigger the effect and loop.
  useEffect(() => {
    if (state.status !== "ready") {
      return;
    }

    const organizationId = state.organization.organization_id;
    const workspaceId = state.workspace.id;

    const alreadyCurrent =
      activeOrganizationId === organizationId &&
      activeWorkspaceId === workspaceId &&
      lastWorkspaceByOrganization[organizationId] === workspaceId;

    if (alreadyCurrent) {
      return;
    }

    setActiveWorkspace(organizationId, workspaceId);
  }, [
    state,
    activeOrganizationId,
    activeWorkspaceId,
    lastWorkspaceByOrganization,
    setActiveWorkspace,
  ]);

  const selectOrganization = useCallback(
    (organizationId: string) => {
      setActiveOrganization(organizationId);
    },
    [setActiveOrganization],
  );

  const selectWorkspace = useCallback(
    (organizationId: string, workspaceId: string) => {
      setActiveWorkspace(organizationId, workspaceId);
    },
    [setActiveWorkspace],
  );

  return {
    state,
    isRefreshing: isFetching && !isLoading,
    refresh: refetch,
    selectOrganization,
    selectWorkspace,
  };
};
