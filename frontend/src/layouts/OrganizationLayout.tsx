import React, { useCallback, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { ChevronRight, Menu, X } from "lucide-react";

import OrganizationSidebarNavigation from "@/components/layout/OrganizationSidebarNavigation";
import ThemeToggle from "@/components/layout/ThemeToggle";
import OrganizationNotificationBell from "@/components/notification/OrganizationNotificationBell";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import DunningBanner from "@/components/billing/DunningBanner";
// ARCH30-T4F:ts-member-notice-import — A5.
import MemberAccessNotice from "@/components/billing/MemberAccessNotice";
import { DisplayPreferencesBoundary } from "@/components/common/DisplayPreferencesBoundary";
import { RouteErrorBoundary } from "@/components/common/RouteErrorBoundary";
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
      <aside className="hidden h-screen w-[260px] min-h-0 flex-shrink-0 flex-col border-r border-border/80 bg-sidebar lg:flex">
        <OrganizationSidebarNavigation onLogout={handleLogout} />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 flex animate-fade-in lg:hidden">
          <div className="flex h-full w-[280px] max-w-[85vw] min-h-0 flex-col border-r border-border bg-sidebar shadow-elevation-3">
            <OrganizationSidebarNavigation
              onNavigate={() => setMobileOpen(false)}
              onLogout={handleLogout}
            />
          </div>
          <div
            className="flex-1 bg-black/60 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="relative z-20 flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border/70 px-3 sm:px-5">
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-background/75 backdrop-blur-md" />
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
              onClick={() => setMobileOpen((open) => !open)}
              aria-label="Toggle organization navigation"
            >
              {mobileOpen ? (
                <X className="h-5 w-5" />
              ) : (
                <Menu className="h-5 w-5" />
              )}
            </button>

            <h1 className="flex min-w-0 items-center gap-1 truncate text-[13px] font-normal">
              <span className="truncate text-muted-foreground">
                {organization.organization_name}
              </span>
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" aria-hidden="true" />
              <span className="font-medium text-foreground">Settings</span>
            </h1>
          </div>

          <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
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

        <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-background p-4 sm:p-5 lg:px-8 lg:py-7">
          <DisplayPreferencesBoundary>
            <RouteErrorBoundary>
              <Outlet />
            </RouteErrorBoundary>
          </DisplayPreferencesBoundary>
        </main>
      </div>
    </div>
  );
};

export default OrganizationLayout;
