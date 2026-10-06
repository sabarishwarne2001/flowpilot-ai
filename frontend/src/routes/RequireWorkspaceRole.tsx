/**
 * Declarative workspace role guard for FlowPilot AI.
 *
 * The client-side counterpart of RequireWorkspaceRole in app/api/deps.py, and
 * it takes a MINIMUM role for the same reason: workspace roles form a true
 * ladder, so ADMIN satisfies any requirement below it.
 *
 * AFFORDANCE ONLY. This decides what renders; the server decides what is
 * allowed. A user who defeats this guard reaches an endpoint that returns 403.
 * Its purpose is preventing a confusing dead end, not enforcing a boundary.
 *
 * The role checked is the EFFECTIVE role from TenantContext, so an
 * organization OWNER or ADMIN passes any workspace requirement through their
 * derived grant. The pre-ARCH-01 checks compared against a stored membership
 * role, which is null for those users — so the most privileged accounts were
 * denied the most controls.
 */

import React from "react";

import { AccessRestricted } from "@/components/common/AccessRestricted";

import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import type { WorkspaceRole } from "@/types/tenancy";

interface RequireWorkspaceRoleProps {
  /** Minimum effective role required. */
  minimum: WorkspaceRole;
  /** Rendered when the requirement is met. */
  children: React.ReactNode;
  /**
   * Rendered instead when it is not.
   *
   * Defaults to an explanatory panel. Pass null to render nothing — correct
   * for inline controls such as a button, where a denial panel inside a
   * toolbar would be noise.
   */
  fallback?: React.ReactNode;
}

const ROLE_WORDS: Readonly<Record<WorkspaceRole, string>> = {
  ADMIN: "workspace administrators",
  CONTRIBUTOR: "workspace contributors and administrators",
  VIEWER: "every workspace member",
};

// F-053: the same Access restricted screen the organization pages use.
const PermissionDenied: React.FC<{ minimum: WorkspaceRole }> = ({ minimum }) => (
  <AccessRestricted allowedFor={ROLE_WORDS[minimum]} askWho="a workspace administrator" />
);

export const RequireWorkspaceRole: React.FC<RequireWorkspaceRoleProps> = ({
  minimum,
  children,
  fallback,
}) => {
  const { workspaceRole } = useResolvedTenant();

  if (!isAtLeast(workspaceRole, minimum)) {
    return <>{fallback === undefined ? <PermissionDenied minimum={minimum} /> : fallback}</>;
  }

  return <>{children}</>;
};

export default RequireWorkspaceRole;
