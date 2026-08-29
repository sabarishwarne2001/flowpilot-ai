import React, { useCallback } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { authApi } from "@/services/api/auth";
import { useAuthStore } from "@/store/useAuthStore";
import { useUIStore } from "@/store/useUIStore";
import { ROUTES } from "@/constants/routes";
import DesktopSidebar from "@/components/layout/DesktopSidebar";
import Sidebar from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { VerificationBanner } from "@/components/common/VerificationBanner";
import IncomingOwnershipBanner from "@/components/organization/IncomingOwnershipBanner";

/**
 * Shell template wrapper encapsulating all gated workspace screens.
 * Strict viewport isolation: Desktop sidebar on lg+, Mobile overlay on <lg.
 */
export const DashboardLayout: React.FC = () => {
  const navigate = useNavigate();

  const clearAuth = useAuthStore((state) => state.clearAuth);
  const isSidebarCollapsed = useUIStore((state) => state.isSidebarCollapsed);

  const handleLogout = useCallback(async (): Promise<void> => {
    await authApi.logoutRequest();
    clearAuth();
    navigate(ROUTES.LOGIN, { replace: true });
  }, [clearAuth, navigate]);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground transition-colors duration-200">
      {/* 1. Desktop Sidebar (Visible ONLY on lg+ screens >= 1024px) */}
      <div
        className={`
          hidden
          h-screen
          shrink-0
          lg:block
          ${isSidebarCollapsed ? "w-20" : "w-64"}
        `}
      >
        <DesktopSidebar onLogout={handleLogout} />
      </div>

      {/* 2. Mobile Drawer (Slide-out overlay, rendered at root viewport level on < 1024px) */}
      <Sidebar onLogout={handleLogout} />

      {/* 3. Main Content Viewport */}
      <div className="flex min-w-0 flex-1 flex-col h-screen overflow-hidden">
        <Header />
        <IncomingOwnershipBanner />
        <VerificationBanner />

        <main className="flex-1 overflow-y-auto bg-muted/10 dark:bg-background p-3 sm:p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default DashboardLayout;
