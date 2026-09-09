import React, { useState } from "react";
import { Outlet } from "react-router-dom";
import { Menu, X } from "lucide-react";

import OrganizationSidebarNavigation from "@/components/layout/OrganizationSidebarNavigation";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";

/**
 * Minimal shell for organization-scoped pages (Billing, Enterprise identity,
 * Audit log).
 *
 * Deliberately NOT DashboardLayout. DashboardLayout and everything it renders
 * (Header, WorkspaceLogo, OrgWorkspaceSwitcher, etc.) is built around a
 * resolved WORKSPACE, which does not exist on these routes -- only an
 * organization does. Reusing it here risks a crash in any descendant that
 * assumes useResolvedTenant() will succeed. This shell reads only from
 * OrganizationGuard's context and renders nothing that assumes a workspace.
 */
export const OrganizationLayout: React.FC = () => {
  const { organization } = useResolvedOrganization();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/*
        Scroll bounding, not decoration.

        This was `hidden lg:block` with no height or overflow of its own. The
        nav inside asks for `h-full`, which only resolves because a block-level
        flex item happens to be stretched to the row's cross size -- an
        implicit dependency on flex stretch semantics that quietly stops
        holding the moment anything here gains a sibling or a wrapper.

        Making it an explicit flex column with `min-h-0` states the intent:
        this column is exactly viewport-tall, and its child owns the scroll.
        `min-h-0` is the load-bearing part -- a flex child defaults to
        `min-height: auto`, so without it the nav can push the column taller
        than the viewport and the scrollbar migrates to the page, which is
        what a second scrollbar next to a sidebar actually is.
      */}
      <aside className="hidden h-screen w-64 min-h-0 flex-shrink-0 flex-col border-r border-border lg:flex">
        <OrganizationSidebarNavigation />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="flex h-full w-64 min-h-0 flex-col border-r border-border bg-background">
            <OrganizationSidebarNavigation onNavigate={() => setMobileOpen(false)} />
          </div>
          <div
            className="flex-1 bg-black/40"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
        </div>
      )}

      <div className="flex flex-1 flex-col overflow-hidden">
        <header className="flex h-14 items-center gap-3 border-b border-border px-4 lg:px-6">
          <button
            type="button"
            className="lg:hidden rounded-lg p-2 hover:bg-muted/50"
            onClick={() => setMobileOpen((open) => !open)}
            aria-label="Toggle organization navigation"
          >
            {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <span className="text-sm font-semibold">
            {organization.organization_name}
          </span>
        </header>

        {/*
          `min-h-0` for the same reason as the aside, and `overscroll-contain`
          so reaching the end of this pane does not chain the scroll to the
          document behind it -- the effect that reads as two scrollbars
          fighting even when only one element is scrollable.
        */}
        <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default OrganizationLayout;
