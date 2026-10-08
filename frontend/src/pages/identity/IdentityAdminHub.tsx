import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Fingerprint,
  Globe2,
  KeyRound,
  Loader2,
  ScrollText,
  ShieldCheck,
  UserPlus,
  Users,
} from "lucide-react";

import AuditExplorer from "@/pages/admin/AuditExplorer";
import DomainManager from "@/pages/identity/DomainManager";
import IdpConnectionBuilder from "@/pages/identity/IdpConnectionBuilder";
import JitPolicyPanel from "@/pages/identity/JitPolicyPanel";
import ScimTokenManager from "@/pages/identity/ScimTokenManager";
import {
  getSecurityPolicy,
  listIdpConfigs,
  updateSecurityPolicy,
} from "@/services/api/identity";
import { errorMessage } from "@/services/api/errors";
import { identityKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { PlanLockBanner } from "@/components/billing/PlanLockBanner";
import { CAPABILITY } from "@/constants/capabilities";
import { AccessRestricted } from "@/components/common/AccessRestricted";
import { PageHeader } from "@/components/ui/PageHeader";
import { organizationMembersPath } from "@/routes/tenantPaths";
import { TabList, TabPanel, useUrlTab, type TabDefinition } from "@/components/ui/Tabs";
import type { SecurityPolicyRead } from "@/types/identity";

type Tab = "domains" | "sso" | "jit" | "scim" | "security" | "audit";

const TABS: readonly TabDefinition<Tab>[] = [
  { id: "domains", label: "Domains", icon: Globe2 },
  { id: "sso", label: "Single sign-on", icon: KeyRound },
  { id: "jit", label: "Provisioning", icon: UserPlus },
  { id: "scim", label: "SCIM", icon: Users },
  { id: "security", label: "Security", icon: ShieldCheck },
  { id: "audit", label: "Audit log", icon: ScrollText },
];
const TAB_IDS = TABS.map((entry) => entry.id);

export const IdentityAdminHub: React.FC = () => {
  const { organization, organizationRole } = useResolvedOrganization();
  const [tab, setTab] = useUrlTab<Tab>(TAB_IDS);

  const role = String(organizationRole).toUpperCase();

  if (role !== "OWNER") {
    return (
      <AccessRestricted
        allowedFor="organization owners (identity and directory settings decide who can sign in)"
        askWho="an organization owner"
        backTo={{ path: organizationMembersPath(organization.organization_slug), label: "Back to members" }}
      />
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-5">
        <PlanLockBanner capability={CAPABILITY.enterpriseIdentity} feature="Enterprise SSO and SCIM" />
        <PageHeader
          icon={Fingerprint}
          eyebrow={organization.organization_name}
          title="Enterprise identity"
          description="Verified domains, single sign-on, just-in-time provisioning, SCIM directory sync and the sign-in policy for every member."
        />

        <TabList label="Identity settings" idBase="identity" tabs={TABS} value={tab} onChange={setTab} />

        <TabPanel idBase="identity" id={tab} className="pt-1">
          {tab === "domains" && <DomainManager />}
          {tab === "sso" && <IdpConnectionBuilder />}
          {tab === "jit" && <JitPolicyPanel />}
          {tab === "scim" && <ScimTokenManager />}
          {tab === "security" && <SecurityPolicyPanel />}
          {tab === "audit" && <AuditExplorer />}
        </TabPanel>
      </div>
    </div>
  );
};

const duration = (seconds: number): string => {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  const parts = [
    hours ? `${hours} ${hours === 1 ? "hour" : "hours"}` : "",
    minutes ? `${minutes} ${minutes === 1 ? "minute" : "minutes"}` : "",
  ].filter(Boolean);
  return parts.join(" ") || "under a minute";
};

const SESSION_CHOICES = [8 * 3600, 4 * 3600, 2 * 3600, 3600];

/**
 * F-204. This read "No maximum session age is set." while every session ended after 12 hours,
 * and an organization's limit set through the API was shown as in force but never applied.
 */
const SessionLifetime: React.FC<{
  readonly policy: SecurityPolicyRead;
  readonly saving: boolean;
  readonly onChange: (seconds: number | null) => void;
}> = ({ policy, saving, onChange }) => {
  const platform = policy.platform_max_session_age_s;
  const own = policy.max_session_age_s;
  const effective = own !== null && (!platform || own < platform) ? own : platform;
  const choices = SESSION_CHOICES.filter((seconds) => !platform || seconds < platform);
  if (own !== null && !choices.includes(own)) {
    choices.unshift(own);
  }

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <label htmlFor="identity-session-lifetime" className="block text-sm font-medium">
        Session lifetime
      </label>
      <p id="identity-session-lifetime-hint" className="mt-0.5 text-xs text-muted-foreground">
        {effective
          ? `Members sign in again ${duration(effective)} after signing in`
          : "Sessions have no maximum age"}
        {policy.idle_timeout_s ? `, or after ${duration(policy.idle_timeout_s)} without activity.` : "."}
        {own !== null && platform && own >= platform
          ? ` Your limit of ${duration(own)} is longer than the platform's, so the platform's applies.`
          : ""}{" "}
        Someone in several organizations gets the shortest limit among them.
      </p>
      <select
        id="identity-session-lifetime"
        aria-describedby="identity-session-lifetime-hint"
        value={own ?? ""}
        disabled={saving}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
        className="mt-2 w-full max-w-xs rounded-md border border-border bg-background px-2.5 py-1.5 text-sm disabled:opacity-50"
      >
        <option value="">
          {platform ? `Platform limit (${duration(platform)})` : "No limit"}
        </option>
        {choices.map((seconds) => (
          <option key={seconds} value={seconds}>
            {duration(seconds)}
          </option>
        ))}
      </select>
      <p className="mt-2 text-xs text-muted-foreground">
        Identity provider session sync: {policy.idp_session_sync ? "on" : "off"}
      </p>
    </section>
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
  // F-188. Requiring SSO with no active identity provider locked every member out; the server
  // now refuses it, and the option says why it is unavailable instead of failing on click.
  const idpQuery = useQuery({
    queryKey: identityKeys.idpConfigs(organizationId),
    queryFn: () => listIdpConfigs(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });
  const hasActiveIdp = (idpQuery.data ?? []).some((config) => config.is_active);
  const [confirmRequire, setConfirmRequire] = useState(false);

  const update = useMutation({
    mutationFn: (patch: Parameters<typeof updateSecurityPolicy>[1]) =>
      updateSecurityPolicy(organizationId, patch),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        identityKeys.securityPolicy(organizationId),
        updated,
      );
      setConfirmBypassOff(false);
      setConfirmRequire(false);
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
            onChange={(e) => {
              if (e.target.checked) {
                setConfirmRequire(true);
                return;
              }
              update.mutate({ require_sso: false });
            }}
            disabled={update.isPending || (!policy.require_sso && !hasActiveIdp)}
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
            {!policy.require_sso && !hasActiveIdp && !idpQuery.isLoading ? (
              <span className="mt-1 block text-xs font-medium text-foreground">
                Connect and activate an identity provider under Single sign-on
                first: with none, nobody could sign in.
              </span>
            ) : null}
          </span>
        </label>

        {confirmRequire && (
          <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
            <p className="flex items-start gap-1.5 text-xs text-foreground">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
              <span>
                Members signed in with a password lose access to this
                organization until they sign in again through your identity
                provider.
                {policy.sso_bypass_for_owners
                  ? " Owners can still use their password (break-glass access is on)."
                  : " This includes owners: break-glass access is off."}
              </span>
            </p>
            <div className="mt-2 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmRequire(false)}
                className="rounded border border-border px-2.5 py-1 text-xs hover:bg-muted"
              >
                Not now
              </button>
              <button
                type="button"
                onClick={() => update.mutate({ require_sso: true })}
                disabled={update.isPending}
                className="rounded bg-primary px-2.5 py-1 text-xs text-primary-foreground disabled:opacity-50"
              >
                Require single sign-on
              </button>
            </div>
          </div>
        )}

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

      <SessionLifetime
        policy={policy}
        saving={update.isPending}
        onChange={(seconds) => update.mutate({ max_session_age_s: seconds })}
      />

      {update.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errorMessage(update.error, "That change wasn't applied. The policy is unchanged.")}
        </p>
      )}
    </div>
  );
};

export default IdentityAdminHub;
