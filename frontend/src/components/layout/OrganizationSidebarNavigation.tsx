import React, { useMemo } from "react";
import { Link, NavLink } from "react-router-dom";
import { ArrowLeft, LogOut } from "lucide-react";

import { buildOrganizationNavigationItems } from "./navigation";
import type { NavigationItem } from "./navigation";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { workspaceDashboardPath } from "@/routes/tenantPaths";
import { useAuthStore } from "@/store/useAuthStore";
import { Avatar } from "@/components/common/Avatar";
import { useTenantStore } from "@/store/useTenantStore";
import { ROUTES } from "@/constants/routes";

interface OrganizationSidebarNavigationProps {
  readonly onNavigate?: () => void;
  readonly onLogout?: () => void;
}

const SECTION_ORDER = [
  "General",
  "Communications & infrastructure",
  "Governance & security",
  "Platform & integrations",
  "Commercial",
  "Other",
] as const;

type SectionName = (typeof SECTION_ORDER)[number];

const SECTION_BY_ITEM: Readonly<Record<string, SectionName>> = {
  General: "General",
  Notifications: "General",
  Members: "General",

  "Transactional email": "Communications & infrastructure",
  "Service levels": "Communications & infrastructure",
  "Branding & custom domains": "Communications & infrastructure",

  "Data governance & compliance": "Governance & security",
  "Enterprise identity": "Governance & security",
  "Audit log": "Governance & security",

  "Developer platform": "Platform & integrations",
  "Enterprise BYOK & models": "Platform & integrations",
  "Analytics & BI egress": "Platform & integrations",
  "Partner marketplace": "Platform & integrations",

  Billing: "Commercial",
  "API keys": "Commercial",
  Webhooks: "Commercial",
};

interface Section {
  readonly name: SectionName;
  readonly items: readonly NavigationItem[];
}

const groupItems = (items: readonly NavigationItem[]): readonly Section[] => {
  const buckets = new Map<SectionName, NavigationItem[]>();
  for (const item of items) {
    const section = SECTION_BY_ITEM[item.name] ?? "Other";
    const bucket = buckets.get(section);
    if (bucket) {
      bucket.push(item);
    } else {
      buckets.set(section, [item]);
    }
  }
  return SECTION_ORDER.flatMap((name) => {
    const bucket = buckets.get(name);
    return bucket && bucket.length > 0 ? [{ name, items: bucket }] : [];
  });
};

const OrganizationSidebarNavigation: React.FC<
  OrganizationSidebarNavigationProps
> = ({ onNavigate, onLogout }) => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();

  const orgSlug = organization.organization_slug;
  const user = useAuthStore((state) => state.user);

  const lastWorkspaceByOrganization = useTenantStore(
    (state) => state.lastWorkspaceByOrganization,
  );

  const items = useMemo(
    () => buildOrganizationNavigationItems(orgSlug, organizationRole),
    [orgSlug, organizationRole],
  );

  const sections = useMemo(() => groupItems(items), [items]);

  const returnTarget = useMemo(() => {
    const reachable = organization.workspaces ?? [];
    if (reachable.length === 0) {
      return null;
    }

    const lastId = lastWorkspaceByOrganization?.[organizationId];
    const last = lastId
      ? reachable.find((workspace) => workspace.id === lastId)
      : undefined;

    const target =
      last ?? reachable.find((workspace) => workspace.status === "ACTIVE");

    if (!target) {
      return null;
    }

    return {
      name: target.workspace_name,
      path: workspaceDashboardPath(orgSlug, target.slug),
    };
  }, [organization.workspaces, lastWorkspaceByOrganization, organizationId, orgSlug]);

  return (
    <div className="flex h-full min-h-0 flex-col" aria-label="Organization Navigation">
      <div className="shrink-0 px-3 pb-3 pt-5">
        {returnTarget ? (
          <Link
            to={returnTarget.path}
            onClick={onNavigate}
            className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm font-semibold text-foreground transition hover:bg-muted/60"
          >
            <ArrowLeft className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate">
              Return to {returnTarget.name}
            </span>
          </Link>
        ) : null}

        <Link
          to={ROUTES.WORKSPACES}
          onClick={onNavigate}
          className="mt-2 block px-1 text-xs text-muted-foreground transition hover:text-foreground hover:underline"
        >
          All organizations &amp; workspaces
        </Link>

        <p className="mt-4 truncate px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {organization.organization_name}
        </p>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-3 pb-4">
        {sections.map((section) => (
          <div key={section.name} className="mb-4 last:mb-0">
            <p className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
              {section.name}
            </p>
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    `group relative flex h-9 items-center rounded-lg px-3 text-sm transition-all ${
                      isActive
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                    }`
                  }
                >
                  <item.icon className="h-4 w-4 flex-shrink-0" />
                  <span className="ml-3 min-w-0 truncate font-medium">
                    {item.name}
                  </span>
                </NavLink>
              ))}
            </div>
          </div>
        ))}

        {items.length === 0 && (
          <p className="px-3 text-xs text-muted-foreground">
            Your role in this organization has no billing or administrative
            surfaces.
          </p>
        )}
      </nav>

      {onLogout ? (
        <div className="shrink-0 border-t border-border/60 px-3 py-3">
          <div className="flex items-center gap-2.5">
            <Avatar userId={user?.id} email={user?.email} size="md" />

            <div className="min-w-0 flex-1">
              <span className="block text-xs font-semibold text-muted-foreground">
                Signed in as
              </span>
              <span className="mt-0.5 block truncate text-sm font-bold leading-none text-foreground">
                {user?.email ?? "User Profile"}
              </span>
            </div>
            <button
              type="button"
              onClick={onLogout}
              title="Sign Out"
              aria-label="Sign Out"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-all hover:bg-destructive/10 hover:text-destructive"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default React.memo(OrganizationSidebarNavigation);
