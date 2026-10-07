import React, { useMemo } from "react";
import { NavLink } from "react-router-dom";
import { Lock } from "lucide-react";

import {
  buildOrganizationNavigationItems,
  buildPartnerNavigationItems,
  buildPlatformNavigationItems,
  buildWorkspaceNavigationGroups,
  type NavigationItem,
} from "./navigation";
import { useGrantedCapabilities } from "@/hooks/useGrantedCapabilities";
import { useIsPartnerMember } from "@/hooks/useIsPartnerMember";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { useIsSuperAdmin } from "@/routes/SuperAdminGuard";
import { SideTooltip } from "@/components/layout/SideTooltip";
import { useUpgradePrompt } from "@/hooks/useUpgradePrompt";

interface SidebarNavigationProps {
  readonly collapsed: boolean;
  readonly onNavigate?: () => void;
}

interface RenderableItem extends NavigationItem {
  readonly end?: boolean;
  readonly locked?: boolean;
  /** HM-S1: the row can be locked, so its lock slot is always reserved. */
  readonly gated?: boolean;
}

/**
 * Primary navigation for the workspace shell.
 *
 * ARCH36-S1:sidebar-groups — what ARCH-36 changed
 * ================================================
 *
 * The workspace list is rendered from `buildWorkspaceNavigationGroups`, in
 * labelled groups. Entries gated by a capability render with a lock when the
 * organization's tier does not grant it and still link to their page, which
 * shows the lock card; see navigation.ts for why they are not hidden. Role
 * filtering reads `minimumRole` from the model instead of comparing paths
 * here, so a second role-gated entry needs no change to this component.
 *
 * While entitlements are loading no lock is drawn. Drawing locks first and
 * removing them a moment later reads as the product changing its mind.
 *
 * Earlier history, still true
 * ===========================
 *
 * ARCH-01 removed the membership query (the role comes from TenantContext,
 * which TenantGuard has already resolved) and the flat route constants (paths
 * are built for the active tenant). Role filtering reads the EFFECTIVE role,
 * so an organization admin holding no stored workspace grant sees the full
 * menu.
 *
 * The organization-scoped group links to /organizations/{slug}/... from inside
 * an ordinary workspace. It is a separate group because it is a different
 * tenancy scope and a different URL tree.
 *
 * ARCH-21 (finding N-2) gave buildPlatformNavigationItems its call site. The
 * platform group renders below the organization group, because a cross-tenant
 * link inside an organization's own navigation invites reading platform-wide
 * totals as that organization's numbers. The builder returns an empty array
 * for a non-superuser, which hides the link; SuperAdminGuard redirects and
 * require_superadmin refuses. Only the last of those is a security control.
 */
const SidebarNavigation: React.FC<SidebarNavigationProps> = ({
  collapsed,
  onNavigate,
}) => {
  const { organization, workspace, workspaceRole, organizationRole } =
    useResolvedTenant();

  const orgSlug = organization.organization_slug;
  const workspaceSlug = workspace.slug;

  const capabilities = useGrantedCapabilities(organization.organization_id);
  const upgrade = useUpgradePrompt(
    organization.organization_id,
    orgSlug,
    String(organizationRole ?? ""),
  );
  const isPartnerMember = useIsPartnerMember();
  const isSuperAdmin = useIsSuperAdmin();

  const groups = useMemo(() => {
    const built = buildWorkspaceNavigationGroups(orgSlug, workspaceSlug);
    return built
      .map((group) => ({
        key: group.key,
        label: group.label,
        items: group.items
          .filter(
            (item) =>
              item.minimumRole === undefined ||
              isAtLeast(workspaceRole, item.minimumRole),
          )
          .map(
            (item): RenderableItem => ({
              name: item.name,
              path: item.path,
              icon: item.icon,
              end: item.end === true,
              ...(item.capability !== undefined ? { capability: item.capability } : {}),
              gated: item.capability !== undefined,
              locked:
                item.capability !== undefined &&
                !capabilities.isLoading &&
                !capabilities.granted.has(item.capability),
            }),
          ),
      }))
      .filter((group) => group.items.length > 0);
  }, [
    orgSlug,
    workspaceSlug,
    workspaceRole,
    capabilities.granted,
    capabilities.isLoading,
  ]);

  const organizationItems = useMemo(
    () => buildOrganizationNavigationItems(orgSlug, String(organizationRole ?? "")),
    [orgSlug, organizationRole],
  );

  const partnerItems = useMemo(
    () => buildPartnerNavigationItems(isPartnerMember),
    [isPartnerMember],
  );

  const platformItems = useMemo(
    () => buildPlatformNavigationItems(isSuperAdmin),
    [isSuperAdmin],
  );

  const renderItem = (item: RenderableItem) => {
    const label = item.locked ? `${item.name} (not included in your plan)` : item.name;
    const shape = collapsed ? "h-9 w-9 justify-center p-0" : "h-8 w-full px-2.5";
    const idle = "text-muted-foreground hover:bg-accent/70 hover:text-foreground";
    const active =
      "bg-primary/10 text-primary hover:bg-primary/[0.14] dark:bg-primary/[0.12] dark:text-[hsl(213_94%_72%)] before:absolute before:-left-2.5 before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-r-full before:bg-primary";

    // HM-S1:lock-slot. A gated row always reserves the lock's width, and the
    // lock only appears once entitlements have loaded, so nothing reflows and
    // nothing flickers when the answer arrives.
    const content = (
      <>
        <item.icon className="h-4 w-4 flex-shrink-0" strokeWidth={1.85} aria-hidden />
        {!collapsed ? (
          <>
            <span className="ml-2.5 min-w-0 flex-1 truncate whitespace-nowrap text-left">
              {item.name}
            </span>
            {item.gated ? (
              <span className="ml-2 flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center" aria-hidden>
                {item.locked && (
                  <Lock className="h-3 w-3 text-muted-foreground/70" aria-hidden data-testid="nav-lock" />
                )}
              </span>
            ) : null}
          </>
        ) : null}
        {collapsed && item.locked && (
          <Lock className="absolute bottom-0.5 right-0.5 h-2.5 w-2.5 text-muted-foreground/80" aria-hidden data-testid="nav-lock" />
        )}
      </>
    );

    // HM-S1:locked-row-opens-upgrade. A locked row is a button that opens the
    // upgrade dialog; it does not navigate to a page whose writes would 402.
    const control =
      item.locked && item.capability ? (
        <button
          key={item.path}
          type="button"
          onClick={() => upgrade.prompt(item.capability as string, item.name)}
          title={collapsed ? label : "Not included in your plan"}
          aria-label={label}
          aria-haspopup="dialog"
          data-testid="nav-locked-row"
          className={`group relative flex items-center rounded-md ${shape} text-[13px] font-medium ${idle} ${item.locked ? "opacity-80" : ""}`}
        >
          {content}
        </button>
      ) : (
        <NavLink
          key={item.path}
          to={item.path}
          onClick={onNavigate}
          end={item.end === true}
          title={collapsed ? label : undefined}
          aria-label={label}
          className={({ isActive }) =>
            `group relative flex items-center rounded-md ${shape} text-[13px] font-medium ${
              isActive ? active : idle
            }`
          }
        >
          {content}
        </NavLink>
      );
    // HARDENING-T1:D7. Collapsed labels render in a portal (see SideTooltip).
    return collapsed ? (
      <SideTooltip key={item.path} label={label}>
        {control}
      </SideTooltip>
    ) : (
      control
    );
  };

  const renderGroupLabel = (label: string, first: boolean) =>
    collapsed ? (
      first ? null : <div className="mx-auto my-2 h-px w-6 bg-border" aria-hidden />
    ) : (
      <p className={`fp-eyebrow px-2.5 pb-1 text-[10.5px] text-muted-foreground/75 ${first ? "pt-1" : "pt-4"}`}>
        {label}
      </p>
    );

  const sectionLabel = (label: string) =>
    collapsed ? (
      <div className="mx-auto mb-2 h-px w-6 bg-border" aria-hidden />
    ) : (
      <p className="fp-eyebrow truncate px-2.5 pb-1 text-[10.5px] text-muted-foreground/75">{label}</p>
    );

  return (
    <nav
      className={`
        flex
        h-full
        min-h-0
        flex-col
        overflow-y-auto
        overflow-x-hidden
        overscroll-contain
        ${collapsed ? "items-center px-2 py-3" : "px-2.5 py-3"}
      `}
      aria-label="Primary Navigation"
    >
      {groups.map((group, index) => (
        <div key={group.key} className={`space-y-0.5 ${collapsed ? "flex flex-col items-center" : ""}`} role="group" aria-label={group.label}>
          {renderGroupLabel(group.label, index === 0)}
          {group.items.map(renderItem)}
        </div>
      ))}

      {organizationItems.length > 0 && (
        <div className={`mt-4 space-y-0.5 pt-1 ${collapsed ? "flex flex-col items-center" : ""}`}>
          {sectionLabel(organization.organization_name)}
          {organizationItems.map((item) =>
            renderItem({
              ...item,
              gated: item.capability !== undefined,
              locked: upgrade.isLocked(item.capability),
            }),
          )}
        </div>
      )}

      {partnerItems.length > 0 && (
        <div className={`mt-4 space-y-0.5 pt-1 ${collapsed ? "flex flex-col items-center" : ""}`}>
          {sectionLabel("Partner")}
          {partnerItems.map((item) => renderItem(item))}
        </div>
      )}

      {platformItems.length > 0 && (
        <div className={`mt-4 space-y-0.5 pt-1 ${collapsed ? "flex flex-col items-center" : ""}`}>
          {sectionLabel("Platform")}
          {platformItems.map((item) => renderItem(item))}
        </div>
      )}
      {upgrade.dialog}
    </nav>
  );
};

export default React.memo(SidebarNavigation);
