import React, { useState, useCallback } from "react";
import { Menu, Sun, Moon, Bell } from "lucide-react";
import { useUIStore } from "@/store/useUIStore";
import { NotificationTray } from "@/components/notification/NotificationTray";

interface HeaderProps {
  /**
   * Optional custom styling overrides to merge with the parent container.
   */
  readonly className?: string;
}

/**
 * Reusable Dashboard Top Header Toolbar Component for FlowPilot AI.
 *
 * Houses responsive menu drawers, global workspace tags, visual theme toggles,
 * and binds click events to toggle the absolute floating Notification Center popover.
 *
 * Performance-optimized via React.memo to isolate rendering loops.
 */
export const Header: React.FC<HeaderProps> = React.memo(
  ({ className = "" }) => {
    // Local state to toggle the visible status of the absolute notification tray
    const [isNotificationsOpen, setIsNotificationsOpen] =
      useState<boolean>(false);

    // Extract reactive states directly from our centralized UI Zustand store
    const { toggleSidebar, theme, toggleTheme, notificationBadgeCount } =
      useUIStore();

    // Memoized toggler handlers to prevent unnecessary component allocations
    const handleToggleNotifications = useCallback((): void => {
      setIsNotificationsOpen((prev) => !prev);
    }, []);

    const handleCloseNotifications = useCallback((): void => {
      setIsNotificationsOpen(false);
    }, []);

    // SaaS Standard: Cap badge metrics to prevent layout leaks under heavy volume alerts
    const displayBadgeCount = React.useMemo(
      () =>
        notificationBadgeCount > 99 ? "99+" : notificationBadgeCount.toString(),
      [notificationBadgeCount]
    );

    return (
      <header
        className={`h-16 border-b border-border/40 flex items-center justify-between px-6 bg-card select-none z-10 transition-colors duration-200 relative ${className}`}
        aria-label="Dashboard Header"
      >
        {/* --- Part 1: Sidebar Toggle Controls & Viewport Name --- */}
        <div className="flex items-center space-x-4">
          {/* Hamburger Drawer Trigger (Visible strictly on mobile/tablet viewports) */}
          <button
            type="button"
            onClick={toggleSidebar}
            className="lg:hidden p-2 rounded-lg text-muted-foreground hover:bg-muted/50 hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all active:scale-[0.97]"
            aria-label="Toggle Navigation Drawer"
          >
            <Menu className="h-5 w-5" />
          </button>

          {/* Dynamic workspace label title */}
          <span className="text-sm font-extrabold tracking-tight text-foreground/90 font-sans">
            Workspace Overview
          </span>
        </div>

        {/* --- Part 2: Global Configuration Actions & Alert Badges --- */}
        <div className="flex items-center space-x-3">
          {/* Visual Light / Dark Theme Mode Trigger Button */}
          <button
            type="button"
            onClick={toggleTheme}
            className="p-2.5 rounded-lg border border-border bg-background hover:bg-muted/50 text-muted-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all"
            aria-label="Toggle Dark Mode Theme"
            title={`Switch to ${theme === "light" ? "Dark" : "Light"} mode`}
          >
            {theme === "light" ? (
              <Moon className="h-4.5 w-4.5 flex-shrink-0" />
            ) : (
              <Sun className="h-4.5 w-4.5 flex-shrink-0" />
            )}
          </button>

          {/* Global Notifications Bell Trigger & Absolute Popover Drawer */}
          <div className="relative">
            <button
              type="button"
              onClick={handleToggleNotifications}
              onKeyDown={(event) => {
                if (event.key === "Escape" && isNotificationsOpen) {
                  handleCloseNotifications();
                }
              }}
              className={`p-2.5 rounded-lg border text-muted-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all relative group
              ${
                isNotificationsOpen
                  ? "bg-muted/50 border-primary/40 text-primary"
                  : "border-border bg-background hover:bg-muted/50"
              }`}
              aria-label="Open Notifications Center"
              aria-expanded={isNotificationsOpen}
              aria-haspopup="dialog"
              title="Open Notifications Panel"
            >
              <Bell className="h-4.5 w-4.5 flex-shrink-0" />

              {/* Pulsing notifications badge indicator */}
              {notificationBadgeCount > 0 && (
                <span
                  className="absolute -top-1 -right-1 h-5 w-5 rounded-full bg-destructive text-destructive-foreground text-[10px] font-black flex items-center justify-center animate-pulse shadow-sm"
                  aria-label={`${notificationBadgeCount} unread alerts`}
                >
                  {displayBadgeCount}
                </span>
              )}
            </button>

            {/* Mount floating popover dropdown aligned relatively to bell boundaries */}
            <NotificationTray
              isOpen={isNotificationsOpen}
              onClose={handleCloseNotifications}
            />
          </div>
        </div>
      </header>
    );
  }
);

// Define explicit displayName metadata for React DevTools memory tracking
Header.displayName = "Header";

export default Header;
