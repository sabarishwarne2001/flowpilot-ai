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
import DunningBanner from "@/components/billing/DunningBanner";
// ARCH30-T4F:ts-member-notice-import-dash — A5.
import MemberAccessNotice from "@/components/billing/MemberAccessNotice";
import { DisplayPreferencesBoundary } from "@/components/common/DisplayPreferencesBoundary";
import { useTenant } from "@/hooks/useTenant";
// ARCH30-T4:ts-mount-tz-import — A3. Mounted on the layout every
// authenticated route renders, because login is not the only way a
// session begins.
import { useTimezoneCapture } from "@/hooks/useTimezoneCapture";

export const DashboardLayout: React.FC = () => {
  const navigate = useNavigate();

  // ARCH30-T4:ts-mount-tz-call — A3. No-op after the first session
  // in which a timezone gets set; see the hook for why it never
  // overwrites a chosen one.
  useTimezoneCapture();

  const clearAuth = useAuthStore((state) => state.clearAuth);
  const beginSignOut = useAuthStore((state) => state.beginSignOut);
  const isSidebarCollapsed = useUIStore((state) => state.isSidebarCollapsed);
  const { state: tenantState } = useTenant();
  const billingOrganizationId =
    tenantState.status === "ready" &&
    ["OWNER", "ADMIN", "BILLING"].includes(String(tenantState.organizationRole).toUpperCase())
      ? tenantState.organization.organization_id
      : null;

  const handleLogout = useCallback(async (): Promise<void> => {
    // ARCH-29 Tranche 1. Set BEFORE the await, not after.
    //
    // `logoutRequest()` revokes the session server-side. Any authenticated
    // request still in flight during that await 401s, the interceptor clears
    // local state, and the guard above this layout re-renders while still
    // mounted at the current deep path — emitting its own redirect to
    // `/login?redirect=<deep path>` before either line below executes. That
    // guard redirect, not this handler, is what put the user back into their
    // settings tab after signing out. Raising the flag first means the guard
    // already knows the exit was deliberate when the race opens.
    beginSignOut();
    try {
      await authApi.logoutRequest();
    } finally {
      // `finally`, because a network failure on the way out must still end the
      // session locally. It also lowers `isSigningOut`: left raised by a failed
      // request, the flag would make the NEXT involuntary expiry discard its
      // destination, which is the bug inverted rather than fixed.
      clearAuth();
      navigate(ROUTES.LOGIN, { replace: true });
    }
  }, [beginSignOut, clearAuth, navigate]);

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
        {billingOrganizationId ? (
          <DunningBanner organizationId={billingOrganizationId} canManageBilling />
        ) : tenantState.status === "ready" ? (
          <MemberAccessNotice organizationId={tenantState.organization.organization_id} />
        ) : null}

        <main className="flex-1 overflow-y-auto bg-muted/10 dark:bg-background p-3 sm:p-4 md:p-6">
          <DisplayPreferencesBoundary>
            <Outlet />
          </DisplayPreferencesBoundary>
        </main>
      </div>
    </div>
  );
};

export default DashboardLayout;
