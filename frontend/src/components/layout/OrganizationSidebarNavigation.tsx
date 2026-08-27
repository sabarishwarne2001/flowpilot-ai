import React, { useMemo } from "react";
import { NavLink } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { buildOrganizationNavigationItems } from "./navigation";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { ROUTES } from "@/constants/routes";

interface OrganizationSidebarNavigationProps {
  readonly onNavigate?: () => void;
}

/**
 * Navigation for organization-scoped pages (Billing, Enterprise identity,
 * Audit log).
 *
 * Deliberately separate from SidebarNavigation, which reads useResolvedTenant()
 * -- a hook that THROWS outside TenantGuard (see TenantContext.tsx). Org-scoped
 * routes mount under OrganizationGuard instead, which resolves an organization
 * but never a workspace, so there is no tenant context to read here. Reusing
 * the workspace sidebar on these routes would crash on the first render.
 *
 * This is the component that makes buildOrganizationNavigationItems reachable
 * at all -- it previously had zero call sites anywhere in the repository.
 */
const OrganizationSidebarNavigation: React.FC<OrganizationSidebarNavigationProps> = ({
  onNavigate,
}) => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();

  const orgSlug = organization.organization_slug;

  const items = useMemo(
    () => buildOrganizationNavigationItems(orgSlug, organizationRole),
    [orgSlug, organizationRole],
  );

  return (
    <nav
      className="flex h-full min-h-0 flex-col px-3 py-5 overflow-y-auto overflow-x-hidden"
      aria-label="Organization Navigation"
    >
      <NavLink
        to={ROUTES.WORKSPACES}
        onClick={onNavigate}
        className="mb-4 flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-muted-foreground hover:bg-muted/50 hover:text-foreground transition"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to workspaces
      </NavLink>

      {!organizationId ? null : (
        <p className="px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {organization.organization_name}
        </p>
      )}

      <div className="space-y-2">
        {items.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={onNavigate}
            className={({ isActive }) =>
              `
                group relative flex items-center h-11 px-3
                rounded-lg text-sm font-medium transition-all
                ${
                  isActive
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                }
              `
            }
          >
            <item.icon className="h-5 w-5 flex-shrink-0" />
            <span className="ml-3 overflow-hidden whitespace-nowrap font-semibold">
              {item.name}
            </span>
          </NavLink>
        ))}
      </div>

      {items.length === 0 && (
        <p className="px-3 text-xs text-muted-foreground">
          Your role in this organization has no billing or administrative
          surfaces.
        </p>
      )}
    </nav>
  );
};

export default React.memo(OrganizationSidebarNavigation);
