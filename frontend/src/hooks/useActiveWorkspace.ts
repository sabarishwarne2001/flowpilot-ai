/**
 * The workspace identifier every scoped page reads.
 *
 * Wraps useTenant so pages do not each re-derive the ready-state narrowing,
 * and so there is exactly one place to change if the tenant state shape moves.
 */

import { useMemo } from "react";

import { useTenant } from "@/hooks/useTenant";

export interface ActiveWorkspace {
  workspaceId: string;
  organizationId: string;
  role: string;
}

/**
 * Returns null until the tenant context resolves.
 *
 * Null rather than throwing: a page renders before resolution on every cold
 * load, and that is normal rather than exceptional. Callers gate their queries
 * with `enabled: Boolean(workspaceId)`.
 */
export const useActiveWorkspace = (): ActiveWorkspace | null => {
  const { state } = useTenant();

  return useMemo(() => {
    if (state.status !== "ready") {
      return null;
    }
    return {
      workspaceId: state.workspace.id,
      organizationId: state.organization.organization_id,
      role: state.workspaceRole, // Safely mapped from the resolved ready state
    };
  }, [state]);
};

/** Convenience for the common case: the id, or undefined while resolving. */
export const useActiveWorkspaceId = (): string | undefined =>
  useActiveWorkspace()?.workspaceId;
