import React, { useCallback, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { Menu, X } from "lucide-react";

import OrganizationSidebarNavigation from "@/components/layout/OrganizationSidebarNavigation";
import ThemeToggle from "@/components/layout/ThemeToggle";
import OrganizationNotificationBell from "@/components/notification/OrganizationNotificationBell";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import DunningBanner from "@/components/billing/DunningBanner";
// ARCH30-T4F:ts-member-notice-import — A5.
import MemberAccessNotice from "@/components/billing/MemberAccessNotice";
import { DisplayPreferencesBoundary } from "@/components/common/DisplayPreferencesBoundary";
import { authApi } from "@/services/api/auth";
import { useAuthStore } from "@/store/useAuthStore";
import { ROUTES } from "@/constants/routes";

export const OrganizationLayout: React.FC = () => {
  const { organization, organizationId, organizationRole } = useResolvedOrganization();
  // ARCH-30 Tranche 3 (D-11). Billing state is visible wherever the tenant
  // works, not only on the Billing page. Limited to roles that can read billing
  // so members do not generate a denied request on every navigation.
  const canSeeBilling = ["OWNER", "ADMIN", "BILLING"].includes(String(organizationRole).toUpperCase());
  const [mobileOpen, setMobileOpen] = useState(false);

  const navigate = useNavigate();
  const clearAuth = useAuthStore((state) => state.clearAuth);
  const beginSignOut = useAuthStore((state) => state.beginSignOut);

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
      <aside className="hidden h-screen w-64 min-h-0 flex-shrink-0 flex-col border-r border-border lg:flex">
        <OrganizationSidebarNavigation onLogout={handleLogout} />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="flex h-full w-64 min-h-0 flex-col border-r border-border bg-background">
            <OrganizationSidebarNavigation
              onNavigate={() => setMobileOpen(false)}
              onLogout={handleLogout}
            />
          </div>
          <div
            className="flex-1 bg-black/40"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-border/60 bg-card px-4 lg:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              className="rounded-lg p-2 hover:bg-muted/50 lg:hidden"
              onClick={() => setMobileOpen((open) => !open)}
              aria-label="Toggle organization navigation"
            >
              {mobileOpen ? (
                <X className="h-5 w-5" />
              ) : (
                <Menu className="h-5 w-5" />
              )}
            </button>

            <h1 className="min-w-0 truncate text-sm">
              <span className="font-semibold text-foreground">
                {organization.organization_name}
              </span>
              <span className="mx-2 text-muted-foreground/60" aria-hidden="true">
                ·
              </span>
              <span className="text-muted-foreground">Settings</span>
            </h1>
          </div>

          <div className="flex shrink-0 items-center gap-2 sm:gap-3">
            <ThemeToggle />
            <OrganizationNotificationBell
              organizationId={organizationId}
              orgSlug={organization.organization_slug}
            />
          </div>
        </header>

        {/* ARCH30-T4F:ts-member-notice-render ? A5. Mutually
            exclusive: a billing-capable role gets the full banner
            with the portal button, everybody else gets the summary
            with no amounts and no actions they cannot take. */}
        {canSeeBilling ? (
          <DunningBanner organizationId={organizationId} canManageBilling />
        ) : (
          <MemberAccessNotice organizationId={organizationId} />
        )}

        <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 lg:p-6">
          <DisplayPreferencesBoundary>
            <Outlet />
          </DisplayPreferencesBoundary>
        </main>
      </div>
    </div>
  );
};

export default OrganizationLayout;
