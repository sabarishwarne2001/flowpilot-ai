import React, { useState } from "react";
import {
  Building2,
  Cpu,
  FileText,
  Mail,
  MonitorSmartphone,
  ShieldAlert,
  UserRound,
} from "lucide-react";

import Workspace from "./Workspace";
import EmailSettings from "./EmailSettings";
import AISettings from "./AISettings";
import DocumentSettings from "./DocumentSettings";
import SessionManagement from "./SessionManagement";
import ProfileSettings from "./ProfileSettings";
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

type SettingsSection =
  | "profile"
  | "workspace"
  | "email"
  | "ai"
  | "document"
  | "sessions";

interface TabConfig {
  id: SettingsSection;
  label: string;
  icon: React.ElementType;
}

const ACCOUNT_SECTIONS = new Set<SettingsSection>(["profile", "sessions"]);

const SETTINGS_TABS: readonly TabConfig[] = [
  { id: "profile", label: "Profile", icon: UserRound },
  { id: "workspace", label: "Workspace", icon: Building2 },
  { id: "email", label: "Email", icon: Mail },
  { id: "ai", label: "AI Settings", icon: Cpu },
  { id: "document", label: "Document Settings", icon: FileText },
  { id: "sessions", label: "Active sessions", icon: MonitorSmartphone },
];

const Settings: React.FC = () => {
  const [activeSection, setActiveSection] = useState<SettingsSection>("profile");
  const { workspaceRole } = useResolvedTenant();

  const canSeeWorkspaceSettings = isAtLeast(workspaceRole, "CONTRIBUTOR");

  const visibleTabs = SETTINGS_TABS.filter(
    (tab) => ACCOUNT_SECTIONS.has(tab.id) || canSeeWorkspaceSettings,
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div>
        <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage your account and workspace configuration.
        </p>
      </div>

      <div className="flex flex-col lg:grid lg:grid-cols-12 gap-6">
        <aside className="lg:col-span-3 rounded-xl border border-border bg-card p-2 sm:p-3 lg:p-4 h-fit">
          <h2 className="hidden lg:block mb-3 text-xs font-bold uppercase tracking-wider text-muted-foreground">
            Configuration
          </h2>

          <nav className="flex flex-row lg:flex-col gap-1.5 overflow-x-auto no-scrollbar pb-1 lg:pb-0">
            {visibleTabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeSection === tab.id;

              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveSection(tab.id)}
                  className={`
                    flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-xs sm:text-sm font-semibold whitespace-nowrap transition-colors
                    ${
                      isActive
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                    }
                  `}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </nav>
        </aside>

        <section className="lg:col-span-9 min-w-0">
          {activeSection === "profile" && <ProfileSettings />}
          {activeSection === "workspace" && (canSeeWorkspaceSettings ? <Workspace /> : <PermissionDenied />)}
          {activeSection === "email" && (canSeeWorkspaceSettings ? <EmailSettings /> : <PermissionDenied />)}
          {activeSection === "ai" && (canSeeWorkspaceSettings ? <AISettings /> : <PermissionDenied />)}
          {activeSection === "document" && (canSeeWorkspaceSettings ? <DocumentSettings /> : <PermissionDenied />)}
          {activeSection === "sessions" && <SessionManagement />}
        </section>
      </div>
    </div>
  );
};

export default Settings;
