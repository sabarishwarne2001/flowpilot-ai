import React, { useMemo } from "react";
import { Link, NavLink } from "react-router-dom";
import { ArrowLeft, LayoutGrid, Lock } from "lucide-react";

import { buildOrganizationNavigationItems } from "./navigation";
import type { NavigationItem } from "./navigation";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { workspaceDashboardPath } from "@/routes/tenantPaths";
import UserMenu from "./UserMenu";
import { useTenantStore } from "@/store/useTenantStore";
import { ROUTES } from "@/constants/routes";
import { useUpgradePrompt } from "@/hooks/useUpgradePrompt";

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
  // ARCH35-S3:autonomy-section
  "Calibrated autonomy": "Governance & security",

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

  const lastWorkspaceByOrganization = useTenantStore(
    (state) => state.lastWorkspaceByOrganization,
  );

  const items = useMemo(
    () => buildOrganizationNavigationItems(orgSlug, organizationRole),
    [orgSlug, organizationRole],
  );

  const sections = useMemo(() => groupItems(items), [items]);
  // HM-S1:org-sidebar-locks
  const upgrade = useUpgradePrompt(organizationId, orgSlug, String(organizationRole));

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
    <div className="flex h-full min-h-0 flex-col bg-sidebar text-sidebar-foreground" aria-label="Organization Navigation">
      <div className="shrink-0 space-y-2 border-b border-border/70 px-2.5 pb-3 pt-3">
        <div className="flex items-center gap-2.5 px-2 py-1">
          <span
            aria-hidden="true"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-primary to-violet-500 text-xs font-semibold text-white shadow-inner-highlight"
          >
            {organization.organization_name.charAt(0).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-semibold leading-5 text-foreground">
              {organization.organization_name}
            </span>
            <span className="block text-[11px] leading-4 text-muted-foreground">Organization console</span>
          </span>
        </div>

        {returnTarget ? (
          <Link
            to={returnTarget.path}
            onClick={onNavigate}
            className="flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-[13px] font-medium text-foreground shadow-elevation-1 hover:border-border-strong hover:bg-accent/60"
          >
            <ArrowLeft className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate">
              Return to {returnTarget.name}
            </span>
          </Link>
        ) : null}

        <Link
          to={ROUTES.WORKSPACES}
          onClick={onNavigate}
          className="flex items-center gap-2 rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground"
        >
          <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
          All organizations &amp; workspaces
        </Link>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-2.5 py-3">
        {sections.map((section) => (
          <div key={section.name} className="mb-4 last:mb-0">
            <p className="fp-eyebrow px-2.5 pb-1 text-[10.5px] text-muted-foreground/75">
              {section.name}
            </p>
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const lockKey: string | undefined = item.capability ?? item.addon;
                const locked = upgrade.isLocked(lockKey);
                const content = (
                  <>
                    <item.icon className="h-4 w-4 flex-shrink-0" strokeWidth={1.85} />
                    <span className="ml-2.5 min-w-0 flex-1 truncate text-left">
                      {item.name}
                    </span>
                    {lockKey !== undefined ? (
                      <span className="ml-2 flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center" aria-hidden>
                        {locked ? (
                          <Lock className="h-3 w-3 text-muted-foreground/70" aria-hidden data-testid="nav-lock" />
                        ) : null}
                      </span>
                    ) : null}
                  </>
                );
                return locked && lockKey ? (
                  <button
                    key={item.path}
                    type="button"
                    onClick={() => upgrade.prompt(lockKey, item.name)}
                    aria-label={`${item.name} (not included in your plan)`}
                    aria-haspopup="dialog"
                    title="Not included in your plan"
                    data-testid="nav-locked-row"
                    className="group relative flex h-8 w-full items-center rounded-md px-2.5 text-[13px] font-medium text-muted-foreground opacity-80 hover:bg-accent/70 hover:text-foreground"
                  >
                    {content}
                  </button>
                ) : (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      `group relative flex h-8 items-center rounded-md px-2.5 text-[13px] font-medium ${
                        isActive
                          ? "bg-primary/10 text-primary hover:bg-primary/[0.14] dark:bg-primary/[0.12] dark:text-[hsl(213_94%_72%)] before:absolute before:-left-2.5 before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-r-full before:bg-primary"
                          : "text-muted-foreground hover:bg-accent/70 hover:text-foreground"
                      }`
                    }
                  >
                    {content}
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}

        {items.length === 0 && (
          <p className="px-3 text-xs text-muted-foreground">
            Your role in this organization has no billing or administrative
            surfaces.
          </p>
        )}
        {upgrade.dialog}
      </nav>

      {onLogout ? (
        <div className="shrink-0 border-t border-border/70 px-2.5 py-2.5">
          <UserMenu
            onLogout={onLogout}
            role={organizationRole}
            profileHref={returnTarget ? `${returnTarget.path}/settings?section=profile` : undefined}
            sessionsHref={returnTarget ? `${returnTarget.path}/settings?section=sessions` : undefined}
          />
        </div>
      ) : null}
    </div>
  );
};

export default React.memo(OrganizationSidebarNavigation);
