/**
 * ARCH40-S2:settings-hub. One settings surface, grouped by scope.
 *
 *   Account        yours, everywhere: profile, sessions
 *   Workspace      this workspace: general, AI defaults, email, documents
 *   Organization   shortcuts to the organization console, for the roles
 *                  that can act there: email, keys and routing, billing
 *                  and spend limits, branding
 *
 * Two columns: the section list on the left, the panel on the right. A
 * section the role cannot see is not listed — not listed-and-locked, because
 * a locked entry here is a control the user can never use from this page.
 * The workspace role is the EFFECTIVE one (TenantGuard already folds in an
 * organization OWNER/ADMIN's implicit elevation), so nothing here compares
 * against a raw membership role.
 */

import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  Building2,
  Cpu,
  CreditCard,
  ExternalLink,
  FileText,
  KeyRound,
  Mail,
  MonitorSmartphone,
  Palette,
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
import { ADMINISTRATIVE_ROLES, canViewBilling } from "@/permissions/organizationPermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import {
  organizationBYOKPath,
  organizationBillingPath,
  organizationBrandingPath,
  organizationEmailPath,
} from "@/routes/tenantPaths";

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

type SettingsSection = "profile" | "sessions" | "workspace" | "ai" | "email" | "document";
type SettingsScope = "account" | "workspace";

interface SectionConfig {
  readonly id: SettingsSection;
  readonly scope: SettingsScope;
  readonly label: string;
  readonly hint: string;
  readonly icon: React.ElementType;
}

const SECTIONS: readonly SectionConfig[] = [
  { id: "profile", scope: "account", label: "Profile", hint: "Name, avatar, email and password", icon: UserRound },
  { id: "sessions", scope: "account", label: "Active sessions", hint: "Where you are signed in", icon: MonitorSmartphone },
  { id: "workspace", scope: "workspace", label: "General", hint: "Name, locale and members", icon: Building2 },
  { id: "ai", scope: "workspace", label: "AI", hint: "What runs, and the defaults", icon: Cpu },
  { id: "email", scope: "workspace", label: "Email", hint: "Sender, relay and why", icon: Mail },
  { id: "document", scope: "workspace", label: "Documents", hint: "Extraction and schema presets", icon: FileText },
];

interface OrgLink {
  readonly label: string;
  readonly hint: string;
  readonly icon: React.ElementType;
  readonly to: (slug: string) => string;
  readonly visible: (role: Parameters<typeof canViewBilling>[0]) => boolean;
}

const ORGANIZATION_LINKS: readonly OrgLink[] = [
  { label: "Organization email", hint: "Transactional sender for every workspace", icon: Mail, to: organizationEmailPath, visible: (r) => ADMINISTRATIVE_ROLES.has(r) },
  { label: "Keys and model routing", hint: "BYOK credentials and routing rules", icon: KeyRound, to: organizationBYOKPath, visible: (r) => ADMINISTRATIVE_ROLES.has(r) },
  { label: "Billing and spend limits", hint: "Plan, invoices and AI spend caps", icon: CreditCard, to: organizationBillingPath, visible: canViewBilling },
  { label: "Branding", hint: "Logo, colors and sender domain", icon: Palette, to: organizationBrandingPath, visible: (r) => ADMINISTRATIVE_ROLES.has(r) },
];

const SCOPE_TITLES: Readonly<Record<SettingsScope, string>> = {
  account: "Your account",
  workspace: "This workspace",
};

const Settings: React.FC = () => {
  const [active, setActive] = useState<SettingsSection>("profile");
  const { workspace, workspaceRole, organization, organizationRole } = useResolvedTenant();

  const canSeeWorkspace = isAtLeast(workspaceRole, "CONTRIBUTOR");
  const visible = SECTIONS.filter((section) => section.scope === "account" || canSeeWorkspace);
  const orgLinks = ORGANIZATION_LINKS.filter((link) => link.visible(organizationRole));
  const slug = organization.organization_slug;

  const panel = (): React.ReactNode => {
    const section = SECTIONS.find((s) => s.id === active);
    if (section?.scope === "workspace" && !canSeeWorkspace) {
      return <PermissionDenied />;
    }
    switch (active) {
      case "profile":
        return <ProfileSettings />;
      case "sessions":
        return <SessionManagement />;
      case "workspace":
        return <Workspace />;
      case "ai":
        return <AISettings />;
      case "email":
        return <EmailSettings />;
      case "document":
        return <DocumentSettings />;
      default:
        return null;
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div>
        <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your account, and <span className="font-semibold text-foreground">{workspace.workspace_name}</span>.
        </p>
      </div>

      <div className="flex flex-col lg:grid lg:grid-cols-12 gap-6">
        <aside className="lg:col-span-3 space-y-5 h-fit" aria-label="Settings sections">
          {(["account", "workspace"] as const).map((scope) => {
            const items = visible.filter((section) => section.scope === scope);
            if (items.length === 0) {
              return null;
            }
            return (
              <nav key={scope} aria-labelledby={`settings-scope-${scope}`} className="rounded-xl border border-border bg-card p-2">
                <h2 id={`settings-scope-${scope}`} className="px-2 pb-1.5 pt-1 text-[10px] font-black uppercase tracking-wider text-muted-foreground">
                  {SCOPE_TITLES[scope]}
                </h2>
                <ul className="flex flex-row gap-1 overflow-x-auto no-scrollbar lg:flex-col">
                  {items.map((section) => {
                    const Icon = section.icon;
                    const selected = active === section.id;
                    return (
                      <li key={section.id}>
                        <button
                          type="button"
                          onClick={() => setActive(section.id)}
                          aria-current={selected ? "page" : undefined}
                          className={`flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left transition-colors ${
                            selected ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                          }`}
                        >
                          <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                          <span className="min-w-0">
                            <span className="block whitespace-nowrap text-sm font-semibold">{section.label}</span>
                            <span className={`hidden text-[11px] lg:block ${selected ? "text-primary-foreground/80" : "text-muted-foreground/80"}`}>
                              {section.hint}
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </nav>
            );
          })}

          {orgLinks.length > 0 && (
            <nav aria-labelledby="settings-scope-organization" className="rounded-xl border border-dashed border-border bg-card/50 p-2">
              <h2 id="settings-scope-organization" className="px-2 pb-1.5 pt-1 text-[10px] font-black uppercase tracking-wider text-muted-foreground">
                Organization
              </h2>
              <ul className="space-y-0.5">
                {orgLinks.map((link) => {
                  const Icon = link.icon;
                  return (
                    <li key={link.label}>
                      <Link
                        to={link.to(slug)}
                        className="flex items-start gap-2.5 rounded-lg px-3 py-2 text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                      >
                        <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                        <span className="min-w-0">
                          <span className="flex items-center gap-1 text-sm font-semibold">
                            {link.label} <ExternalLink className="h-3 w-3" aria-hidden="true" />
                          </span>
                          <span className="hidden text-[11px] text-muted-foreground/80 lg:block">{link.hint}</span>
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>
          )}
        </aside>

        <section className="lg:col-span-9 min-w-0">{panel()}</section>
      </div>
    </div>
  );
};

export default Settings;
