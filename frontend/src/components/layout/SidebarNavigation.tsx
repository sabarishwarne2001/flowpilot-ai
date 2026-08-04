import React from "react";
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { ROUTES } from "@/constants/routes";
import { NAVIGATION_ITEMS } from "./navigation";
import { getMyMembership } from "@/services/api/workspace";

interface SidebarNavigationProps {
  readonly collapsed: boolean;
  readonly onNavigate?: () => void;
}

const SidebarNavigation: React.FC<SidebarNavigationProps> = ({
  collapsed,
  onNavigate,
}) => {
  // Query active membership role to filter visibility of settings dynamically
  const { data: myMembership } = useQuery({
    queryKey: ["workspace_membership_me"],
    queryFn: getMyMembership,
    retry: false,
  });

  const role = myMembership?.role;

  // Viewers cannot inspect settings tabs
  const filteredNavigationItems = NAVIGATION_ITEMS.filter((item) => {
    if (item.path === ROUTES.SETTINGS && role === "VIEWER") {
      return false;
    }
    return true;
  });

  return (
    <nav
      className={`
        flex
        h-full
        min-h-0
        flex-col
        px-3
        py-5
        overflow-y-auto
        overflow-x-hidden
        ${collapsed ? "items-center" : ""}
      `}
      aria-label="Primary Navigation"
    >
      <div className="space-y-2">
        {filteredNavigationItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={onNavigate}
            end={item.path === ROUTES.DASHBOARD}
            title={collapsed ? item.name : undefined}
            className={({ isActive }) =>
              `
                group relative
                flex items-center
                justify-center
                rounded-lg
                ${collapsed ? "h-11 w-11 p-0" : "h-11 px-3"}
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

            {!collapsed ? (
              <span
                className={`
                  ml-3
                  overflow-hidden
                  whitespace-nowrap
                  font-semibold
                  transition-all
                  duration-300
                  ease-in-out
                  ${collapsed ? "w-0 opacity-0" : "w-auto opacity-100"}
                `}
              >
                {item.name}
              </span>
            ) : (
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
                  transition-opacity
                  duration-150
                  group-hover:opacity-100
                  group-focus-visible:opacity-100
                "
              >
                {item.name}
              </span>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  );
};

export default React.memo(SidebarNavigation);
