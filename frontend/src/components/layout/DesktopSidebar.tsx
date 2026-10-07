import React from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { useUIStore } from "@/store/useUIStore";
import { useResolvedTenant } from "@/routes/TenantContext";
import { workspaceSettingsPath } from "@/routes/tenantPaths";
import SidebarNavigation from "./SidebarNavigation";
import OrgWorkspaceSwitcher from "./OrgWorkspaceSwitcher";
import UserMenu from "./UserMenu";
import { SideTooltip } from "./SideTooltip";

interface DesktopSidebarProps {
  readonly onLogout: () => void;
  readonly className?: string;
}

/**
 * The workspace sidebar: 260px expanded, a 68px icon rail collapsed.
 *
 * Top: the workspace identity, which is also the switcher. Middle: grouped
 * navigation. Bottom: the signed-in user's card and menu. The collapse toggle
 * sits beside the identity (expanded) or under it (rail), and the choice is
 * remembered (useUIStore persists it).
 */
const DesktopSidebarComponent: React.FC<DesktopSidebarProps> = ({
  onLogout,
  className = "",
}) => {
  const { isSidebarCollapsed: collapsed, toggleSidebarCollapse } = useUIStore();
  const { organization, workspace, organizationRole } = useResolvedTenant();
  const settings = workspaceSettingsPath(organization.organization_slug, workspace.slug);

  const toggle = (
    <button
      type="button"
      onClick={toggleSidebarCollapse}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
      aria-label={collapsed ? "Expand Sidebar" : "Collapse Sidebar"}
      aria-expanded={!collapsed}
    >
      {collapsed ? (
        <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
      ) : (
        <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
      )}
    </button>
  );

  return (
    <aside
      aria-label="Primary Navigation Sidebar"
      className={`
        relative flex h-full min-h-0 flex-col
        border-r border-border/80 bg-sidebar text-sidebar-foreground
        transition-[width] duration-200 ease-out-expo
        ${collapsed ? "w-[68px]" : "w-[260px]"}
        ${className}
      `}
    >
      {/* Identity + collapse */}
      <div
        className={`flex shrink-0 border-b border-border/70 ${
          collapsed ? "flex-col items-center gap-1.5 px-2 py-2.5" : "h-14 items-center gap-1 px-2.5"
        }`}
      >
        <div className={collapsed ? "" : "min-w-0 flex-1"}>
          <OrgWorkspaceSwitcher collapsed={collapsed} />
        </div>
        {collapsed ? <SideTooltip label="Expand sidebar">{toggle}</SideTooltip> : toggle}
      </div>

      {/* Navigation */}
      <div className="min-h-0 flex-1">
        <SidebarNavigation collapsed={collapsed} />
      </div>

      {/* The signed-in user */}
      <div className={`shrink-0 border-t border-border/70 ${collapsed ? "px-2 py-2.5" : "px-2.5 py-2.5"}`}>
        <UserMenu
          onLogout={onLogout}
          collapsed={collapsed}
          role={organizationRole}
          profileHref={`${settings}?section=profile`}
          sessionsHref={`${settings}?section=sessions`}
        />
      </div>
    </aside>
  );
};

export const DesktopSidebar = React.memo(DesktopSidebarComponent);
DesktopSidebar.displayName = "DesktopSidebar";

export default DesktopSidebar;
