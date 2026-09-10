/**
 * ARCH-29 Tranche 1 — chrome for the platform administration surface.
 *
 * THE PROBLEM THIS CLOSES
 * =======================
 *
 * `SuperAdminGuard` returns a bare `<Outlet />`. It is a guard and nothing
 * else, which is correct — but nothing else wrapped the route either, so
 * `/admin/margins` rendered with no header, no navigation and no way back.
 * The only exit was the browser URL bar.
 *
 * WHY NOT JUST NEST IT UNDER THE ORGANIZATION SHELL
 * =================================================
 *
 * Because `SuperAdminGuard` already refuses that, and its reasoning holds:
 *
 *   > mounting it inside an organization shell would make cross-tenant totals
 *   > appear to belong to whichever organization the user last had selected,
 *   > which is exactly the misreading the COGS dashboard must not invite.
 *
 * `AdminMarginsHub` reads across every tenant. Rendered inside
 * `Caretakers global Inc`'s sidebar, with that organization's name in the
 * header and its workspace switcher one click away, a platform-wide gross
 * margin becomes Caretakers' gross margin to anyone reading quickly. The page
 * spends its entire implementation refusing to state a number it cannot
 * support — suppressing the headline margin when too little revenue has a
 * known cost basis — and then the chrome would silently misattribute the
 * numbers it does state.
 *
 * So the escape hatch is a SEPARATE shell, and it is deliberately unlike
 * tenant chrome: no workspace switcher, no organization name, an explicit
 * scope band, and a single exit that leaves the platform surface entirely
 * rather than pretending to be a peer of the workspace navigation.
 *
 * WHY THE SCOPE BAND IS PART OF THE LAYOUT AND NOT THE PAGE
 * =========================================================
 *
 * A future ARCH-30 platform page inherits it by mounting here. If the band
 * lived in `AdminMarginsHub`, the second platform page would ship without it,
 * and the misattribution this layout exists to prevent would return on a
 * surface nobody re-audited.
 *
 * SECURITY POSTURE — UNCHANGED
 * ============================
 *
 * This component is chrome. It renders below `SuperAdminGuard`, which is
 * itself a navigation affordance and not a control; `require_superadmin` on
 * the backend is the authority, and it returns 404 rather than 403 to avoid
 * being an enumeration oracle. Nothing here is load-bearing for access.
 */

import React from "react";
import { Link, Outlet } from "react-router-dom";
import { ArrowLeft, Globe2, ShieldAlert } from "lucide-react";

import ThemeToggle from "@/components/layout/ThemeToggle";
import { ROUTES } from "@/constants/routes";

export const PlatformLayout: React.FC = () => {
  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-background text-foreground transition-colors duration-200">
      <header className="flex-shrink-0 border-b border-border bg-card">
        <div className="flex items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              to={ROUTES.WORKSPACES}
              className="
                inline-flex
                flex-shrink-0
                items-center
                gap-1.5
                rounded-md
                border
                border-border
                bg-background
                px-3
                py-1.5
                text-sm
                font-medium
                text-foreground
                transition-colors
                hover:bg-muted
                focus:outline-none
                focus:ring-2
                focus:ring-primary/30
              "
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Back to workspaces
            </Link>

            <div className="hidden h-6 w-px flex-shrink-0 bg-border sm:block" />

            <div className="flex min-w-0 items-center gap-2">
              <ShieldAlert
                className="h-4 w-4 flex-shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
              <span className="truncate text-sm font-semibold tracking-tight">
                Platform administration
              </span>
            </div>
          </div>

          <div className="flex flex-shrink-0 items-center gap-2">
            <ThemeToggle />
          </div>
        </div>

        {/*
          The scope band. Deliberately not dismissible: the misreading it
          guards against is most likely on a return visit, when the novelty of
          the page has worn off and the numbers look like any other dashboard.
        */}
        <div className="flex items-start gap-2 border-t border-border bg-muted/40 px-4 py-2 sm:px-6">
          <Globe2
            className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <p className="text-xs leading-relaxed text-muted-foreground">
            These figures span{" "}
            <strong className="font-semibold text-foreground">
              every organization on this deployment
            </strong>
            . They are not scoped to any tenant you belong to.
          </p>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto bg-muted/10 p-3 dark:bg-background sm:p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
};

export default PlatformLayout;
