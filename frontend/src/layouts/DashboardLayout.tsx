import React, { useCallback } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { authApi } from "@/services/api/auth";
import { useAuthStore } from "@/store/useAuthStore";
import { useUIStore } from "@/store/useUIStore";
import { ROUTES } from "@/constants/routes";
import Sidebar from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";

/**
 * Shell template wrapper encapsulating all gated workspace screens.
 *
 * Composes our decoupled, memory-optimized Sidebar navigation panel and
 * top-level Header toolbar, managing responsive viewports and session terminations.
 */
export const DashboardLayout: React.FC = () => {
  const navigate = useNavigate();

  // Extract state getters from Zustand stores
  const clearAuth = useAuthStore((state) => state.clearAuth);
  const isSidebarCollapsed = useUIStore(
    (state) => state.isSidebarCollapsed
  );

  /**
   * Action handler to safely clear session stores and return to Login endpoint.
   *
   * Memoized with useCallback to maintain Sidebar's React.memo rendering optimizations.
   */
  const handleLogout = useCallback(async (): Promise<void> => {
    // Revoke the refresh session server-side FIRST, then clear locally.
    // Clearing first would drop the in-memory token, but the refresh cookie
    // is what actually keeps the session alive — a purely local sign-out
    // would leave a fourteen-day credential live in the browser, and the next
    // page load would silently restore the session the user just ended.
    await authApi.logoutRequest();

    // Clear local Zustand state persistent session records
    clearAuth();

    // Shift client viewport replacing historical entries stack to block back-actions
    navigate(ROUTES.LOGIN, { replace: true });
  }, [clearAuth, navigate]);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground transition-colors duration-200">
      {/* --- Part 1: Collapsible Sidebar Drawer Panel --- */}
      <div
        className={`
          h-screen
          shrink-0
          ${isSidebarCollapsed ? "lg:w-20" : "lg:w-64"}
        `}
      >
        <Sidebar onLogout={handleLogout} />
      </div>

      {/* --- Part 2: Main Workspace Canvas Area (Toolbar + Outlet Subview) --- */}
      <div className="flex flex-col flex-1 min-w-0 h-screen overflow-hidden">
        {/* Consolidated top layout header bar */}
        <Header />

        {/* Core dynamic Main viewpoint scrolling container */}
        <main className="flex-1 overflow-y-auto bg-muted/10 dark:bg-background p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default DashboardLayout;
