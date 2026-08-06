import React, { useState } from "react";
import { ShieldAlert } from "lucide-react";

import Workspace from "./Workspace";
import EmailSettings from "./EmailSettings";
import AISettings from "./AISettings";
import DocumentSettings from "./DocumentSettings";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";

export const PermissionDenied: React.FC = () => {
  return (
    <div className="flex h-[60vh] flex-col items-center justify-center p-6 text-center select-none animate-fade-in">
      <div className="p-4 bg-destructive/10 text-destructive rounded-full mb-4">
        <ShieldAlert className="h-10 w-10" />
      </div>
      <h2 className="text-lg font-extrabold tracking-tight">Permission Denied</h2>
      <p className="text-xs text-muted-foreground font-semibold leading-relaxed mt-2 max-w-sm">
        You do not possess sufficient privilege levels to inspect or modify workspace settings in this role.
      </p>
    </div>
  );
};

/**
 * Workspace settings shell.
 *
 * ARCH-01 removed the membership query that previously gated this page. It
 * called GET /workspace/members/me — deleted in the backend transformation —
 * and every settings tab waited behind a LoadingScreen for a request that
 * always 404s.
 *
 * The role now comes from TenantContext, already resolved by TenantGuard
 * before this component mounts. There is no request and therefore no loading
 * state.
 *
 * The role read here is the EFFECTIVE role, so an organization admin holding
 * no stored workspace grant reaches settings. The previous check compared
 * against a stored membership, which is null for those users — the most
 * privileged accounts were the ones it locked out.
 */
const Settings: React.FC = () => {
  const [activeSection, setActiveSection] = useState<
    "workspace" | "email" | "ai" | "document"
  >("workspace");

  const { workspaceRole } = useResolvedTenant();

  // Intercept and prevent VIEWER role manual URL accesses
  if (!isAtLeast(workspaceRole, "CONTRIBUTOR")) {
    return <PermissionDenied />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>

        <p className="mt-2 text-muted-foreground">
          Manage your workspace configuration.
        </p>
      </div>

      <div className="grid grid-cols-12 gap-6">
        {/* Left Sidebar */}
        <aside className="col-span-3 rounded-xl border border-border bg-card p-4">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Configuration
          </h2>

          <nav className="space-y-2">
            <button
              onClick={() => setActiveSection("workspace")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "workspace"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              Workspace
            </button>

            <button
              onClick={() => setActiveSection("email")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "email"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              Email
            </button>

            <button
              onClick={() => setActiveSection("ai")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "ai"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              AI Settings
            </button>

            <button
              onClick={() => setActiveSection("document")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "document"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              Document Settings
            </button>
          </nav>
        </aside>

        {/* Right Content */}
        <section className="col-span-9">
          {activeSection === "workspace" && <Workspace />}
          {activeSection === "email" && <EmailSettings />}
          {activeSection === "ai" && <AISettings />}
          {activeSection === "document" && <DocumentSettings />}
        </section>
      </div>
    </div>
  );
};

export default Settings;
