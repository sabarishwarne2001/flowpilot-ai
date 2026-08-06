/**
 * Permission surface for the FlowPilot AI frontend.
 *
 * Namespaced rather than flattened, deliberately. Several names exist at both
 * tiers with genuinely different meanings — `precedence` is an administrative
 * ordering for organizations and an access ladder for workspaces — and a flat
 * re-export would let a caller pick the wrong one silently.
 *
 * Usage:
 *
 *   import { workspacePermissions } from "@/permissions";
 *   const canEdit = workspacePermissions.canManageWorkspaceSettings(role);
 */

export * as organizationPermissions from "@/permissions/organizationPermissions";
export * as workspacePermissions from "@/permissions/workspacePermissions";

export {
  resolveEffectiveWorkspaceRole,
  hasWorkspaceAccess,
} from "@/permissions/workspacePermissions";

export {
  runPermissionSelfCheck,
  assertPermissionParity,
} from "@/permissions/selfCheck";
