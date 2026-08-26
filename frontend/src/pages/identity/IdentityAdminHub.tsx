import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Lock } from "lucide-react";

import AuditExplorer from "@/pages/admin/AuditExplorer";
import DomainManager from "@/pages/identity/DomainManager";
import IdpConnectionBuilder from "@/pages/identity/IdpConnectionBuilder";
import JitPolicyPanel from "@/pages/identity/JitPolicyPanel";
import ScimTokenManager from "@/pages/identity/ScimTokenManager";
import {
  getSecurityPolicy,
  updateSecurityPolicy,
} from "@/services/api/identity";
import { identityKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";

type Tab = "domains" | "sso" | "jit" | "scim" | "security" | "audit";

const TABS: readonly { id: Tab; label: string }[] = [
  { id: "domains", label: "Domains" },
  { id: "sso", label: "Single sign-on" },
  { id: "jit", label: "Provisioning" },
  { id: "scim", label: "SCIM" },
  { id: "security", label: "Security" },
  { id: "audit", label: "Audit log" },
];

export const IdentityAdminHub: React.FC = () => {
  const { organization, organizationRole } = useResolvedOrganization();
  const [tab, setTab] = useState<Tab>("domains");

  const role = String(organizationRole).toUpperCase();

  if (role !== "OWNER") {
    return (
      <div className="mx-auto max-w-md p-8 text-center">
        <Lock
          className="mx-auto h-6 w-6 text-muted-foreground"
          aria-hidden="true"
        />
        <h1 className="mt-3 text-base font-semibold">Owners only</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Identity and directory settings can only be managed by an organization
          owner.
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl p-4 sm:p-6">
        <header>
          <h1 className="text-xl font-semibold">Enterprise identity</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {organization.organization_name}
          </p>
        </header>

        <nav
          role="tablist"
          aria-label="Identity settings"
          className="mt-4 flex flex-wrap gap-1 border-b border-border"
        >
          {TABS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={tab === entry.id}
              onClick={() => setTab(entry.id)}
              className={[
                "-mb-px border-b-2 px-3 py-2 text-sm",
                tab === entry.id
                  ? "border-primary font-medium text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              {entry.label}
            </button>
          ))}
        </nav>

        <div className="py-5">
          {tab === "domains" && <DomainManager />}
          {tab === "sso" && <IdpConnectionBuilder />}
          {tab === "jit" && <JitPolicyPanel />}
          {tab === "scim" && <ScimTokenManager />}
          {tab === "security" && <SecurityPolicyPanel />}
          {tab === "audit" && <AuditExplorer />}
        </div>
      </div>
    </div>
  );
};

const SecurityPolicyPanel: React.FC = () => {
  const { organizationId } = useResolvedOrganization();
  const queryClient = useQueryClient();
  const [confirmBypassOff, setConfirmBypassOff] = useState(false);

  const policyQuery = useQuery({
    queryKey: identityKeys.securityPolicy(organizationId),
    queryFn: () => getSecurityPolicy(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const update = useMutation({
    mutationFn: (patch: Parameters<typeof updateSecurityPolicy>[1]) =>
      updateSecurityPolicy(organizationId, patch),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        identityKeys.securityPolicy(organizationId),
        updated,
      );
      setConfirmBypassOff(false);
    },
  });

  if (policyQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading policy…
      </div>
    );
  }

  const policy = policyQuery.data;
  if (!policy) {
    return null;
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-border bg-card p-4">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={policy.require_sso}
            onChange={(e) => update.mutate({ require_sso: e.target.checked })}
            disabled={update.isPending}
            className="mt-0.5 h-4 w-4 rounded border-border"
          />
          <span>
            <span className="block text-sm font-medium">
              Require single sign-on
            </span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              Members must sign in through your identity provider. Passwords
              stop working.
            </span>
          </span>
        </label>

        <div className="mt-3 border-t border-border pt-3">
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              checked={policy.sso_bypass_for_owners}
              onChange={(e) => {
                if (!e.target.checked) {
                  setConfirmBypassOff(true);
                  return;
                }
                update.mutate({ sso_bypass_for_owners: true });
              }}
              disabled={update.isPending}
              className="mt-0.5 h-4 w-4 rounded border-border"
            />
            <span>
              <span className="block text-sm font-medium">
                Owners can bypass SSO
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                Break-glass access. Keep this on unless you have another way in.
              </span>
            </span>
          </label>

          {confirmBypassOff && (
            <div className="mt-2 rounded-md border border-destructive/50 bg-destructive/5 p-3">
              <p className="flex items-start gap-1.5 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  <strong className="font-medium">
                    If your identity provider becomes unavailable, nobody will
                    be able to sign in — including you.
                  </strong>{" "}
                  There is no self-service recovery from this.
                </span>
              </p>
              <div className="mt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmBypassOff(false)}
                  className="rounded border border-border px-2.5 py-1 text-xs hover:bg-muted"
                >
                  Keep bypass on
                </button>
                <button
                  type="button"
                  onClick={() =>
                    update.mutate({ sso_bypass_for_owners: false })
                  }
                  disabled={update.isPending}
                  className="rounded bg-destructive px-2.5 py-1 text-xs text-destructive-foreground disabled:opacity-50"
                >
                  I understand — turn it off
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-border bg-card p-4 opacity-70">
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={policy.ip_pinning !== "OFF"}
            disabled
            className="mt-0.5 h-4 w-4 rounded border-border"
          />
          <div>
            <span className="block text-sm font-medium">
              IP pinning{" "}
              <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-[11px] font-normal">
                unavailable
              </span>
            </span>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Currently {policy.ip_pinning}. This cannot be enabled until the
              trusted proxy configuration is confirmed for your deployment.
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-card p-4">
        <span className="block text-sm font-medium">Session lifetime</span>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {policy.max_session_age_s
            ? `Sessions end after ${Math.round(policy.max_session_age_s / 3600)} hours.`
            : "No maximum session age is set."}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Identity provider session sync:{" "}
          {policy.idp_session_sync ? "on" : "off"}
        </p>
      </section>

      {update.isError && (
        <p role="alert" className="text-sm text-destructive">
          That change wasn&apos;t applied. The policy is unchanged.
        </p>
      )}
    </div>
  );
};

export default IdentityAdminHub;
