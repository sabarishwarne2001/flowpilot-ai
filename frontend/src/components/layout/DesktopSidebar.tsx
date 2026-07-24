import React from "react";

import { ChevronLeft, LogOut } from "lucide-react";

import { useAuthStore } from "@/store/useAuthStore";
import { useUIStore } from "@/store/useUIStore";
import { Brand } from "@/components/branding/Brand";
import SidebarNavigation from "./SidebarNavigation";

interface DesktopSidebarProps {
  /**
   * Parent-controlled logout callback.
   */
  readonly onLogout: () => void;

  readonly className?: string | undefined;
}

/**
 * Reusable application navigation sidebar.
 */
const DesktopSidebarComponent: React.FC<DesktopSidebarProps> = ({
  onLogout,
  className = "",
}) => {
  const { user } = useAuthStore();

  const { isSidebarCollapsed, toggleSidebarCollapse } = useUIStore();

  const isDesktopCollapsed = isSidebarCollapsed;

  /**
   * Placeholder for future async logout flow.
   */
  const isLoggingOut = false;

  return (
    <aside
      aria-label="Primary Navigation Sidebar"
      className={`
        relative
        flex
        h-full
        min-h-0
        flex-col
        bg-card
        border-r
        border-border
        transition-[width]
        duration-300
        ease-in-out
        ${isDesktopCollapsed ? "w-20" : "w-64"}
        ${className}
      `}
    >
      {/* ======================================================
          Top Section
      ====================================================== */}
      <div className="flex min-h-0 flex-1 flex-col">
        {/* ======================================================
            Brand Header
        ====================================================== */}
        {!isDesktopCollapsed ? (
          <div className="flex h-[72px] items-center justify-between border-b border-border/40 px-4">
            <Brand variant="sidebar" className="min-w-0 flex-1" />

            {/* Desktop Collapse */}
            <button
              type="button"
              onClick={toggleSidebarCollapse}
              className="ml-2 flex h-8 w-8 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
              aria-label="Collapse Sidebar"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <div className="flex h-[88px] flex-col items-center justify-center gap-3 border-b border-border/40 pt-3">
            <Brand variant="sidebar-compact" />

            {/* Desktop Expand */}
            <button
              type="button"
              onClick={toggleSidebarCollapse}
              className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
              aria-label="Expand Sidebar"
            >
              <ChevronLeft className="h-4 w-4 rotate-180" />
            </button>
          </div>
        )}

        {/* Navigation */}
        <div className="flex-1 min-h-0">
          <SidebarNavigation collapsed={isDesktopCollapsed} />
        </div>
      </div>

      {/* ======================================================
          Bottom Profile Section
      ====================================================== */}
      <div className="border-t border-border/40 bg-muted/20 px-4 py-5 dark:bg-muted/5">
        <div
          className={`
            flex
            ${
              isDesktopCollapsed
                ? "flex-col items-center gap-3"
                : "items-center justify-between"
            }
          `}
        >
          <div
            className={`
              min-w-0
              overflow-hidden
              transition-all
              duration-300
              ease-in-out
              ${
                isDesktopCollapsed
                  ? "max-w-0 opacity-0"
                  : "max-w-[220px] opacity-100"
              }
            `}
          >
            <span className="block truncate text-xs font-semibold text-muted-foreground select-none">
              Signed in as
            </span>

            <span className="mt-1 block truncate text-sm font-extrabold leading-none">
              {user?.email ?? "User Profile"}
            </span>
          </div>

          {/* Logout Button */}
          <button
            type="button"
            onClick={onLogout}
            disabled={isLoggingOut}
            title="Sign Out"
            aria-label="Sign Out"
            className={`
              flex
              h-10
              w-10
              items-center
              justify-center
              rounded-lg
              text-muted-foreground
              transition-all
              duration-300
              hover:bg-destructive/10
              hover:text-destructive
              focus:outline-none
              focus:ring-2
              focus:ring-destructive/20
              disabled:pointer-events-none
              disabled:opacity-50
            `}
          >
            <LogOut className="h-5 w-5 flex-shrink-0" />
          </button>
        </div>
      </div>
    </aside>
  );
};

/**
 * Memoized Sidebar component.
 *
 * Prevents unnecessary re-renders when parent layouts update while
 * the Sidebar props and Zustand state remain unchanged.
 */
export const DesktopSidebar = React.memo(DesktopSidebarComponent);

DesktopSidebar.displayName = "DesktopSidebar";

export default DesktopSidebar;
