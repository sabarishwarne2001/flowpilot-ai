import React, { useCallback } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { useAuthStore } from "@/store/useAuthStore";
import { useUIStore } from "@/store/useUIStore";
import { ROUTES } from "@/constants/routes";
import { Sidebar } from "@/components/layout/Sidebar";
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
  const isSidebarOpen = useUIStore((state) => state.isSidebarOpen);
  const toggleSidebar = useUIStore((state) => state.toggleSidebar);

  /**
   * Action handler to safely clear session stores and return to Login endpoint.
   *
   * Memoized with useCallback to maintain Sidebar's React.memo rendering optimizations.
   */
  const handleLogout = useCallback((): void => {
    // ----------------------------------------------------------------------
    // SPRINT 7 NOTE:
    // Revoke target active session tokens securely inside the DB if requested:
    // await authApi.logoutRequest();
    // ----------------------------------------------------------------------

    // Clear local Zustand state persistent session records
    clearAuth();

    // Shift client viewport replacing historical entries stack to block back-actions
    navigate(ROUTES.LOGIN, { replace: true });
  }, [clearAuth, navigate]);

  return (
    <div className="min-h-dvh flex bg-background text-foreground transition-colors duration-200 overflow-hidden">
      {/* --- Part 1: Collapsible Sidebar Drawer Panel --- */}
      <Sidebar onLogout={handleLogout} />

      {/* Backdrop overlay visible strictly under active mobile drawer displays */}
      {isSidebarOpen && (
        <div
          onClick={toggleSidebar}
          onKeyDown={(e) => e.key === "Escape" && toggleSidebar()}
          className="fixed inset-0 z-30 bg-black/40 backdrop-blur-sm lg:hidden cursor-pointer"
          aria-label="Close navigation sidebar"
          role="button"
          tabIndex={0}
        />
      )}

      {/* --- Part 2: Main Workspace Canvas Area (Toolbar + Outlet Subview) --- */}
      <div className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
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
