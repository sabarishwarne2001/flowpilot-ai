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
import { ShieldAlert } from "lucide-react";

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

const PermissionDenied: React.FC<{ minimum: WorkspaceRole }> = ({ minimum }) => (
  <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-border/60 bg-muted/10 px-6 py-12 text-center">
    <ShieldAlert className="h-6 w-6 text-muted-foreground" />
    <div className="space-y-1">
      <p className="text-sm font-semibold text-foreground">
        You do not have access to this section
      </p>
      <p className="text-xs text-muted-foreground">
        This requires the {minimum.toLowerCase()} role or higher in this
        workspace. Ask a workspace admin if you need access.
      </p>
    </div>
  </div>
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
