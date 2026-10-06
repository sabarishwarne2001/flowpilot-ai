/**
 * F-008 / F-054 — route-level organization role guard.
 *
 * Under /organizations/:orgSlug/*, OrganizationGuard checks membership and
 * archived status but not role, so a MEMBER who deep-linked to an admin page
 * got the page, its requests were refused with 403, and it said "… couldn't be
 * loaded. Try again" (Branding even showed its whole edit form). This renders
 * the Access restricted screen instead, before any request is made.
 *
 * AFFORDANCE ONLY, like RequireWorkspaceRole: the server refuses every call.
 * The allowed roles match who the organization sidebar shows each page to
 * (navigation.ts), so a link a role can see is a page it can open.
 */

import React from "react";

import { AccessRestricted } from "@/components/common/AccessRestricted";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { organizationSettingsPath } from "@/routes/tenantPaths";
import type { OrganizationRole } from "@/types/tenancy";

/** Organization pages and the roles that may open them. */
export const OWNERS_AND_ADMINS: readonly OrganizationRole[] = ["OWNER", "ADMIN"];
export const OWNERS_ONLY: readonly OrganizationRole[] = ["OWNER"];
export const OWNERS_AND_BILLING: readonly OrganizationRole[] = ["OWNER", "BILLING"];

const ROLE_WORDS: Readonly<Record<OrganizationRole, string>> = {
  OWNER: "owners",
  ADMIN: "administrators",
  BILLING: "billing managers",
  MEMBER: "members",
};

const describe = (roles: readonly OrganizationRole[]): string => {
  const words = roles.map((role) => ROLE_WORDS[role]);
  if (words.length <= 1) {
    return `organization ${words[0] ?? "owners"}`;
  }
  return `organization ${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
};

const askWho = (roles: readonly OrganizationRole[]): string =>
  roles.includes("ADMIN") ? "an organization owner or administrator" : "an organization owner";

interface RequireOrganizationRoleProps {
  readonly allowed: readonly OrganizationRole[];
  readonly children: React.ReactNode;
}

export const RequireOrganizationRole: React.FC<RequireOrganizationRoleProps> = ({ allowed, children }) => {
  const { organization, organizationRole } = useResolvedOrganization();
  const role = String(organizationRole).toUpperCase() as OrganizationRole;

  if (!allowed.includes(role)) {
    return (
      <AccessRestricted
        allowedFor={describe(allowed)}
        askWho={askWho(allowed)}
        backTo={{ path: organizationSettingsPath(organization.organization_slug), label: "Back to organization settings" }}
      />
    );
  }
  return <>{children}</>;
};

export default RequireOrganizationRole;
