import React from "react";
import { NavLink } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  FileText,
  MessageSquare,
  Settings,
  Sliders,
  User as UserIcon,
  ChevronLeft,
  LogOut,
} from "lucide-react";

import { useAuthStore } from "@/store/useAuthStore";
import { useUIStore } from "@/store/useUIStore";
import { ROUTES } from "@/constants/routes";

/**
 * TODO (Future Module):
 * Replace duplicated branding with a reusable <Logo /> component
 * shared across AuthLayout, Sidebar, Header, etc.
 */

/**
 * Strongly typed navigation model.
 */
interface NavigationItem {
  readonly name: string;
  readonly path: string;
  readonly icon: LucideIcon;
}

/**
 * Static navigation configuration.
 * Declared outside the component to avoid recreation on every render.
 */
const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  {
    name: "Overview",
    path: ROUTES.DASHBOARD,
    icon: LayoutDashboard,
  },
  {
    name: "Documents",
    path: ROUTES.WORK_ITEMS,
    icon: FileText,
  },
  {
    name: "AI Assistant",
    path: ROUTES.ASSISTANT,
    icon: MessageSquare,
  },
  {
    name: "Workflows",
    path: ROUTES.AUTOMATION,
    icon: Sliders,
  },
  {
    name: "Settings",
    path: ROUTES.SETTINGS,
    icon: Settings,
  },
];

interface SidebarProps {
  /**
   * Parent-controlled logout callback.
   */
  readonly onLogout: () => void;

  readonly className?: string;
}

/**
 * Reusable application navigation sidebar.
 */
const SidebarComponent: React.FC<SidebarProps> = ({
  onLogout,
  className = "",
}) => {
  const { user } = useAuthStore();

  const { isSidebarOpen, toggleSidebar } = useUIStore();

  /**
   * Placeholder for future async logout flow.
   */
  const isLoggingOut = false;
  return (
    <aside
      aria-label="Primary Navigation Sidebar"
      className={`
        fixed inset-y-0 left-0 z-40
        h-screen
        bg-card
        border-r border-border
        flex flex-col justify-between
        transition-all duration-300
        lg:static lg:translate-x-0
        ${
          isSidebarOpen
            ? "translate-x-0 w-64"
            : "-translate-x-full pointer-events-none lg:pointer-events-auto lg:translate-x-0 w-20"
        }
        ${className}
      `}
    >
      {/* ======================================================
          Top Section
      ====================================================== */}

      <div>
        {/* Brand Header */}
        <div className="flex h-16 items-center justify-between border-b border-border/40 px-6 select-none">
          <div className="flex items-center space-x-2">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-primary shadow-md">
              <span className="font-mono text-base font-black text-primary-foreground">
                FP
              </span>
            </div>

            {isSidebarOpen && (
              <span className="text-lg font-black tracking-tight font-sans transition-opacity duration-200">
                FlowPilot
                <span className="font-black text-primary">AI</span>
              </span>
            )}
          </div>

          {/* Desktop Collapse Button */}
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={isSidebarOpen ? "Collapse Sidebar" : "Expand Sidebar"}
            className="hidden h-7 w-7 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 lg:flex"
          >
            <ChevronLeft
              className={`h-4 w-4 transition-transform duration-300 ${
                !isSidebarOpen ? "rotate-180" : ""
              }`}
            />
          </button>
        </div>

        {/* Navigation */}
        <nav className="space-y-1.5 p-4" aria-label="Primary Navigation">
          {NAVIGATION_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === ROUTES.DASHBOARD}
              title={!isSidebarOpen ? item.name : undefined}
              className={({ isActive }) =>
                `
                group relative
                flex items-center
                rounded-lg
                px-3 py-2.5
                text-sm font-medium
                transition-all
                ${
                  isActive
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                }
              `
              }
            >
              <item.icon className="h-5 w-5 flex-shrink-0" />

              {isSidebarOpen ? (
                <span className="ml-3 font-semibold transition-opacity duration-200">
                  {item.name}
                </span>
              ) : (
                /**
                 * Accessible tooltip for collapsed navigation.
                 * Visible on both mouse hover and keyboard focus.
                 */
                <span
                  className="
                    pointer-events-none
                    absolute left-16 z-50
                    whitespace-nowrap
                    rounded-md
                    border border-border
                    bg-card
                    px-2.5 py-1.5
                    text-xs font-semibold
                    text-foreground
                    shadow-md
                    opacity-0
                    transition-opacity duration-150
                    group-hover:opacity-100
                    group-focus-visible:opacity-100
                  "
                >
                  {item.name}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* ======================================================
          Bottom Profile Section
      ====================================================== */}

      <div className="border-t border-border/40 bg-muted/20 p-4 dark:bg-muted/5">
        <div
          className={`flex items-center ${
            isSidebarOpen ? "justify-between" : "justify-center"
          }`}
        >
          {isSidebarOpen && (
            <>
              {/**
               * TODO (Future Module):
               * Replace with reusable <UserAvatar />
               * component shared across the application.
               */}
              <div className="flex min-w-0 items-center space-x-3 overflow-hidden">
                <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/10 font-bold text-primary">
                  {user?.email?.charAt(0).toUpperCase() ?? (
                    <UserIcon className="h-4 w-4" />
                  )}
                </div>

                <div className="flex min-w-0 flex-col">
                  <span className="truncate text-xs font-semibold text-muted-foreground select-none">
                    Signed in as
                  </span>

                  <span className="mt-1 truncate text-sm font-extrabold leading-none">
                    {user?.email ?? "User Profile"}
                  </span>
                </div>
              </div>
            </>
          )}
          {/* Logout Button */}
          <button
            type="button"
            onClick={onLogout}
            disabled={isLoggingOut}
            title="Sign Out"
            aria-label="Sign Out"
            className={`
              rounded-lg
              p-2
              text-muted-foreground
              transition-all
              hover:bg-destructive/10
              hover:text-destructive
              focus:outline-none
              focus:ring-2
              focus:ring-destructive/20
              disabled:pointer-events-none
              disabled:opacity-50
              ${
                !isSidebarOpen
                  ? "flex h-10 w-10 items-center justify-center"
                  : ""
              }
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
export const Sidebar = React.memo(SidebarComponent);

Sidebar.displayName = "Sidebar";

export default Sidebar;
