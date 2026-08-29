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
import PendingInvitationsBanner from "@/components/invitations/PendingInvitationsBanner";

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

      <Sidebar onLogout={handleLogout} />

      <div className="flex min-w-0 flex-1 flex-col h-screen overflow-hidden">
        <Header />
        <IncomingOwnershipBanner />
        <PendingInvitationsBanner />
        <VerificationBanner />

        <main className="flex-1 overflow-y-auto bg-muted/10 dark:bg-background p-3 sm:p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default DashboardLayout;
