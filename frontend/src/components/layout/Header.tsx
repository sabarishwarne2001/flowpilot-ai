import React, { useCallback, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Bell, ChevronRight, Menu, Moon, Search, Sun } from "lucide-react";

import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useUIStore } from "@/store/useUIStore";
import { NotificationTray } from "@/components/notification/NotificationTray";
import { useResolvedTenant } from "@/routes/TenantContext";
import { workspaceDashboardPath } from "@/routes/tenantPaths";
import {
  buildOrganizationNavigationItems,
  buildWorkspaceNavigationGroups,
} from "./navigation";
import { commandPaletteShortcutLabel, openCommandPalette } from "./commandPaletteEvents";

interface HeaderProps {
  readonly className?: string;
}

/**
 * The page label for the breadcrumb: the navigation entry whose path is the
 * longest prefix of the current URL. The navigation model is the one source of
 * page names, so a renamed entry renames its breadcrumb too.
 */
function usePageLabel(): string | null {
  const { pathname } = useLocation();
  const { organization, workspace, organizationRole } = useResolvedTenant();
  return useMemo(() => {
    const orgSlug = organization.organization_slug;
    const items = [
      ...buildWorkspaceNavigationGroups(orgSlug, workspace.slug).flatMap((group) => group.items),
      ...buildOrganizationNavigationItems(orgSlug, String(organizationRole ?? "")),
    ];
    let best: { name: string; length: number } | null = null;
    for (const item of items) {
      const exact = pathname === item.path;
      const nested = pathname.startsWith(`${item.path}/`);
      const isOverview = item.path === workspaceDashboardPath(orgSlug, workspace.slug);
      if ((exact || (nested && !isOverview)) && (!best || item.path.length > best.length)) {
        best = { name: item.name, length: item.path.length };
      }
    }
    return best?.name ?? null;
  }, [pathname, organization.organization_slug, workspace.slug, organizationRole]);
}

export const Header: React.FC<HeaderProps> = React.memo(({ className = "" }) => {
  const [isNotificationsOpen, setIsNotificationsOpen] = useState<boolean>(false);
  const { toggleMobileSidebar, resolvedTheme, toggleTheme, notificationBadgeCount } = useUIStore();
  const { organization, workspace } = useResolvedTenant();
  const pageLabel = usePageLabel();
  useDocumentTitle(pageLabel === "Overview" ? null : pageLabel, workspace.workspace_name);
  const shortcut = commandPaletteShortcutLabel();

  const handleToggleNotifications = useCallback((): void => {
    setIsNotificationsOpen((prev) => !prev);
  }, []);

  const handleCloseNotifications = useCallback((): void => {
    setIsNotificationsOpen(false);
  }, []);

  const displayBadgeCount = notificationBadgeCount > 99 ? "99+" : notificationBadgeCount.toString();
  const dashboard = workspaceDashboardPath(organization.organization_slug, workspace.slug);

  const iconButton =
    "relative flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground";

  return (
    <header
      className={`relative z-20 flex h-14 shrink-0 select-none items-center justify-between gap-3 border-b border-border/70 px-3 sm:px-5 ${className}`}
      aria-label="Dashboard Header"
    >
      {/* The glass is a layer behind the bar, not a filter on it: a filter on an
          ancestor would become the containing block of any fixed-position
          overlay a header control opens. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-background/75 backdrop-blur-md" />
      <div className="flex min-w-0 items-center gap-2">
        {/* Only below 1024px: the sidebar is a drawer there. */}
        <button
          type="button"
          onClick={toggleMobileSidebar}
          className={`${iconButton} lg:hidden`}
          aria-label="Toggle Navigation Drawer"
        >
          <Menu className="h-[18px] w-[18px]" />
        </button>

        <nav aria-label="Breadcrumb" className="min-w-0">
          <ol className="flex min-w-0 items-center gap-1 text-[13px]">
            <li className="hidden min-w-0 truncate text-muted-foreground md:block">
              {organization.organization_name}
            </li>
            <li aria-hidden="true" className="hidden text-muted-foreground/50 md:block">
              <ChevronRight className="h-3.5 w-3.5" />
            </li>
            <li className="min-w-0 truncate">
              {pageLabel && pageLabel !== "Overview" ? (
                <Link to={dashboard} className="rounded text-muted-foreground hover:text-foreground">
                  {workspace.workspace_name}
                </Link>
              ) : (
                <span className="font-medium text-foreground" aria-current="page">
                  {workspace.workspace_name}
                </span>
              )}
            </li>
            {pageLabel && pageLabel !== "Overview" && (
              <>
                <li aria-hidden="true" className="text-muted-foreground/50">
                  <ChevronRight className="h-3.5 w-3.5" />
                </li>
                <li className="min-w-0 truncate font-medium text-foreground" aria-current="page">
                  {pageLabel}
                </li>
              </>
            )}
          </ol>
        </nav>
      </div>

      <div className="flex items-center gap-1.5 sm:gap-2">
        <button
          type="button"
          onClick={openCommandPalette}
          aria-label={`Search pages (${shortcut})`}
          aria-keyshortcuts="Control+K Meta+K"
          className="group flex h-8 items-center gap-2 rounded-lg border border-border bg-card/60 px-2 text-[13px] text-muted-foreground shadow-elevation-1 hover:border-border-strong hover:text-foreground sm:w-56 sm:px-2.5 lg:w-64"
        >
          <Search className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="hidden flex-1 truncate text-left sm:block">Search or jump to…</span>
          <kbd className="fp-kbd hidden sm:inline-flex">{shortcut}</kbd>
        </button>

        <div className="mx-0.5 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />

        <button
          type="button"
          onClick={toggleTheme}
          className={iconButton}
          aria-label="Toggle Theme"
          title={`Switch to ${resolvedTheme === "light" ? "dark" : "light"} mode`}
        >
          {resolvedTheme === "light" ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
        </button>

        <div className="relative">
          <button
            type="button"
            onClick={handleToggleNotifications}
            onKeyDown={(event) => {
              if (event.key === "Escape" && isNotificationsOpen) {
                handleCloseNotifications();
              }
            }}
            className={`${iconButton} ${isNotificationsOpen ? "bg-accent text-foreground" : ""}`}
            aria-label="Open Notifications Center"
            aria-expanded={isNotificationsOpen}
            aria-haspopup="dialog"
            title="Notifications"
          >
            <Bell className="h-4 w-4" />
            {notificationBadgeCount > 0 && (
              <span
                className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-destructive-foreground ring-2 ring-background"
                aria-label={`${notificationBadgeCount} unread alerts`}
              >
                {displayBadgeCount}
              </span>
            )}
          </button>

          <NotificationTray isOpen={isNotificationsOpen} onClose={handleCloseNotifications} />
        </div>
      </div>
    </header>
  );
});

Header.displayName = "Header";

export default Header;
