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
      <aside className="hidden lg:block w-64 flex-shrink-0 border-r border-border">
        <OrganizationSidebarNavigation />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="w-64 bg-background border-r border-border">
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

        <main className="flex-1 overflow-y-auto p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default OrganizationLayout;
