import React, { useState, useCallback } from "react";
import { Menu, Sun, Moon, Bell } from "lucide-react";
import { useUIStore } from "@/store/useUIStore";
import { NotificationTray } from "@/components/notification/NotificationTray";

interface HeaderProps {
  readonly className?: string;
}

export const Header: React.FC<HeaderProps> = React.memo(
  ({ className = "" }) => {
    const [isNotificationsOpen, setIsNotificationsOpen] = useState<boolean>(false);

    const {
      toggleMobileSidebar,
      theme,
      toggleTheme,
      notificationBadgeCount,
    } = useUIStore();

    const handleToggleNotifications = useCallback((): void => {
      setIsNotificationsOpen((prev) => !prev);
    }, []);

    const handleCloseNotifications = useCallback((): void => {
      setIsNotificationsOpen(false);
    }, []);

    const displayBadgeCount = React.useMemo(
      () =>
        notificationBadgeCount > 99 ? "99+" : notificationBadgeCount.toString(),
      [notificationBadgeCount],
    );

    return (
      <header
        className={`h-16 shrink-0 border-b border-border/40 flex items-center justify-between px-3 sm:px-6 bg-card select-none z-10 transition-colors duration-200 relative ${className}`}
        aria-label="Dashboard Header"
      >
        {/* Left Toggle — ONLY visible on mobile/tablet (< 1024px), hidden on desktop */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={toggleMobileSidebar}
            className="lg:hidden p-2 rounded-lg text-muted-foreground hover:bg-muted/50 hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all active:scale-[0.97]"
            aria-label="Toggle Navigation Drawer"
          >
            <Menu className="h-5 w-5" />
          </button>
        </div>

        {/* Right Configuration Actions */}
        <div className="flex items-center space-x-2 sm:space-x-3">
          <button
            type="button"
            onClick={toggleTheme}
            className="p-2 sm:p-2.5 rounded-lg border border-border bg-background hover:bg-muted/50 text-muted-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all"
            aria-label="Toggle Theme"
            title={`Switch to ${theme === "light" ? "Dark" : "Light"} mode`}
          >
            {theme === "light" ? (
              <Moon className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
            ) : (
              <Sun className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
            )}
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
              className={`p-2 sm:p-2.5 rounded-lg border text-muted-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all relative group
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
              <Bell className="h-4 w-4 sm:h-4.5 sm:w-4.5" />

              {notificationBadgeCount > 0 && (
                <span
                  className="absolute -top-1 -right-1 h-4.5 w-4.5 sm:h-5 sm:w-5 rounded-full bg-destructive text-destructive-foreground text-[10px] font-black flex items-center justify-center animate-pulse shadow-sm"
                  aria-label={`${notificationBadgeCount} unread alerts`}
                >
                  {displayBadgeCount}
                </span>
              )}
            </button>

            <NotificationTray
              isOpen={isNotificationsOpen}
              onClose={handleCloseNotifications}
            />
          </div>
        </div>
      </header>
    );
  },
);

Header.displayName = "Header";

export default Header;
