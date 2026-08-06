/**
 * Tenant selection store for FlowPilot AI.
 *
 * Persists WHICH tenant the actor is operating in — never the tenant data
 * itself. Organization and workspace records come from /me/context on every
 * boot, so a rename, a role change, or a removal takes effect immediately
 * rather than surviving in localStorage.
 *
 * That distinction matters most for roles. A persisted role would be used to
 * decide which buttons to render, so a demoted administrator would keep seeing
 * administrative controls until they happened to clear their browser storage.
 * Identifiers are inert: they mean nothing without server data to resolve them
 * against, and resolution validates them on every load.
 */

import { create } from "zustand";
import { createJSONStorage, devtools, persist } from "zustand/middleware";

import { EMPTY_SELECTION } from "@/hooks/tenantResolution";
import type { TenantSelection } from "@/hooks/tenantResolution";

const TENANT_STORAGE_KEY = "flowpilot_tenant_selection";

interface TenantStoreState extends TenantSelection {
  /**
   * Selects an organization.
   *
   * Clears the active workspace: the previous one belongs to a different
   * organization and would be discarded by resolution anyway. Clearing it
   * explicitly means the per-organization memory decides, which is what makes
   * switching back land where you left off.
   */
  readonly setActiveOrganization: (organizationId: string) => void;

  /**
   * Selects a workspace and records it as this organization's last-visited.
   */
  readonly setActiveWorkspace: (
    organizationId: string,
    workspaceId: string,
  ) => void;

  /**
   * Clears the selection, preserving per-organization memory.
   *
   * Used when resolution finds the current selection no longer valid — a
   * removed membership or an archived workspace. The memory is kept because
   * the actor may be re-added, and it is validated on every resolution in any
   * case.
   */
  readonly clearActiveSelection: () => void;

  /**
   * Clears everything, including per-organization memory.
   *
   * Called on sign-out. Leaving the memory behind would mean the next person
   * to sign in on this machine starts with a stranger's tenant identifiers —
   * harmless because resolution validates them, but a needless disclosure of
   * where the previous user worked.
   */
  readonly resetTenantSelection: () => void;
}

export const useTenantStore = create<TenantStoreState>()(
  devtools(
    persist(
      (set) => ({
        ...EMPTY_SELECTION,

        setActiveOrganization: (organizationId) =>
          set((state) => ({
            ...state,
            activeOrganizationId: organizationId,
            activeWorkspaceId: null,
          })),

        setActiveWorkspace: (organizationId, workspaceId) =>
          set((state) => ({
            ...state,
            activeOrganizationId: organizationId,
            activeWorkspaceId: workspaceId,
            lastWorkspaceByOrganization: {
              ...state.lastWorkspaceByOrganization,
              [organizationId]: workspaceId,
            },
          })),

        clearActiveSelection: () =>
          set((state) => ({
            ...state,
            activeOrganizationId: null,
            activeWorkspaceId: null,
          })),

        resetTenantSelection: () =>
          set((state) => ({
            ...state,
            ...EMPTY_SELECTION,
          })),
      }),
      {
        name: TENANT_STORAGE_KEY,

        storage: createJSONStorage(() => localStorage),

        // Identifiers only. Never tenant records, never roles.
        partialize: (state) => ({
          activeOrganizationId: state.activeOrganizationId,
          activeWorkspaceId: state.activeWorkspaceId,
          lastWorkspaceByOrganization: state.lastWorkspaceByOrganization,
        }),
      },
    ),
    {
      name: "TenantStore",
    },
  ),
);

/**
 * Reads the current selection without subscribing.
 *
 * For use outside React — the auth store's sign-out path, for instance.
 */
export const getTenantSelection = (): TenantSelection => {
  const state = useTenantStore.getState();
  return {
    activeOrganizationId: state.activeOrganizationId,
    activeWorkspaceId: state.activeWorkspaceId,
    lastWorkspaceByOrganization: state.lastWorkspaceByOrganization,
  };
};
