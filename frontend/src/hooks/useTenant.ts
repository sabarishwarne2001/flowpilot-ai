/**
 * The single source of truth for tenant context in the FlowPilot AI UI.
 */

import { useCallback, useEffect, useMemo } from "react";
import { useLocation } from "react-router-dom";

import { useMeContext } from "@/hooks/useMeContext";
import { NO_ROUTE_LOCATOR, resolveTenant } from "@/hooks/tenantResolution";
import type { TenantState } from "@/hooks/tenantResolution";
import { parseTenantPath } from "@/routes/tenantPaths";
import { useAuthStore } from "@/store/useAuthStore";
import { useTenantStore } from "@/store/useTenantStore";

export interface UseTenantResult {
  state: TenantState;
  isRefreshing: boolean;
  refresh: () => void;
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

  const { pathname } = useLocation();

  const route = useMemo(() => {
    const parsed = parseTenantPath(pathname);
    return parsed
      ? { orgSlug: parsed.orgSlug, workspaceSlug: parsed.workspaceSlug }
      : NO_ROUTE_LOCATOR;
  }, [pathname]);

  const state = useMemo(
    () =>
      resolveTenant({
        isAuthenticated,
        isLoading,
        isUnauthorized,
        error,
        context,
        selection,
        route,
      }),
    [isAuthenticated, isLoading, isUnauthorized, error, context, selection, route],
  );

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
