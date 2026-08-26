import React, { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Play, Plus, ShieldCheck } from "lucide-react";

import {
  activateIdpConfig,
  addCertificate,
  addRoleMapping,
  createIdpConfig,
  dryRunRoleMapping,
  listDomains,
  listIdpConfigs,
} from "@/services/api/identity";
import { identityKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import type {
  DryRunResult,
  IdpConfigCreate,
  IdpProtocol,
  JitProvisioningMode,
  OrganizationRoleName,
  RoleMatchKind,
} from "@/types/identity";

const ROLES: readonly OrganizationRoleName[] = ["MEMBER", "BILLING", "ADMIN"];
const MATCH_KINDS: readonly RoleMatchKind[] = ["EQUALS", "CONTAINS", "PREFIX"];

export const IdpConnectionBuilder: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const queryClient = useQueryClient();
  const isOwner = String(organizationRole).toUpperCase() === "OWNER";

  const [creating, setCreating] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const configsQuery = useQuery({
    queryKey: identityKeys.idpConfigs(organizationId),
    queryFn: () => listIdpConfigs(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const domainsQuery = useQuery({
    queryKey: identityKeys.domains(organizationId),
    queryFn: () => listDomains(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const invalidate = useCallback(
    () =>
      queryClient.invalidateQueries({
        queryKey: identityKeys.idpConfigs(organizationId),
      }),
    [queryClient, organizationId],
  );

  const activate = useMutation({
    mutationFn: (configId: string) =>
      activateIdpConfig(organizationId, configId),
    onSuccess: invalidate,
  });

  const configs = configsQuery.data ?? [];
  const verifiedDomains = (domainsQuery.data ?? []).filter(
    (domain) => String(domain.status).toUpperCase() === "VERIFIED",
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Identity providers</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Connect SAML 2.0 or OIDC so your team signs in with your own
            directory.
          </p>
        </div>

        {isOwner && verifiedDomains.length > 0 && !creating && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:opacity-90"
          >
            <Plus className="h-3.5 w-3.5" />
            Add connection
          </button>
        )}
      </div>

      {verifiedDomains.length === 0 && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-800">
          You need a verified domain before connecting an identity provider.
          Verify one under Domains first.
        </p>
      )}

      {creating && (
        <ConnectionForm
          organizationId={organizationId}
          domains={verifiedDomains.map((d) => ({ id: d.id, domain: d.domain }))}
          onCancel={() => setCreating(false)}
          onCreated={async (id) => {
            setCreating(false);
            setSelectedId(id);
            await invalidate();
          }}
        />
      )}

      {configsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading connections…</p>
      ) : configs.length === 0 && !creating ? (
        <p className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
          No identity providers connected.
        </p>
      ) : (
        <ul className="space-y-2">
          {configs.map((config) => (
            <li
              key={config.id}
              className="rounded-lg border border-border bg-card"
            >
              <button
                type="button"
                onClick={() =>
                  setSelectedId(selectedId === config.id ? null : config.id)
                }
                aria-expanded={selectedId === config.id}
                className="flex w-full items-center gap-3 p-3 text-left"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">
                      {config.display_name}
                    </span>
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                      {config.protocol}
                    </span>
                    {config.is_active ? (
                      <span className="inline-flex items-center gap-1 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[11px] text-emerald-700">
                        <ShieldCheck className="h-3 w-3" />
                        Active
                      </span>
                    ) : (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                        Not activated
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {config.jit_provisioning_mode} ·{" "}
                    {config.current_billable_seats} seats
                    {config.effective_seat_cap !== null &&
                      ` of ${config.effective_seat_cap}`}
                  </p>
                </div>
              </button>

              {selectedId === config.id && (
                <div className="space-y-4 border-t border-border p-3">
                  <CertificatePanel
                    organizationId={organizationId}
                    configId={config.id}
                    canEdit={isOwner}
                  />

                  <RoleMappingPanel
                    organizationId={organizationId}
                    configId={config.id}
                    canEdit={isOwner}
                  />

                  {isOwner && !config.is_active && (
                    <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                      <p className="flex items-start gap-1.5 text-xs text-amber-800">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <span>
                          Activating makes this the sign-in path for everyone
                          with a matching email domain. Test your role mappings
                          above first — a wrong mapping under an uncapped policy
                          adds billable seats at your provider&apos;s timing.
                        </span>
                      </p>
                      <button
                        type="button"
                        onClick={() => activate.mutate(config.id)}
                        disabled={activate.isPending}
                        className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:opacity-90 disabled:opacity-50"
                      >
                        {activate.isPending && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        )}
                        Activate connection
                      </button>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

interface ConnectionFormProps {
  readonly organizationId: string;
  readonly domains: readonly { id: string; domain: string }[];
  readonly onCancel: () => void;
  readonly onCreated: (configId: string) => void;
}

const ConnectionForm: React.FC<ConnectionFormProps> = ({
  organizationId,
  domains,
  onCancel,
  onCreated,
}) => {
  const [protocol, setProtocol] = useState<IdpProtocol>("SAML2");
  const [displayName, setDisplayName] = useState("");
  const [domainId, setDomainId] = useState(domains[0]?.id ?? "");
  const [metadataUrl, setMetadataUrl] = useState("");
  const [entityId, setEntityId] = useState("");
  const [ssoUrl, setSsoUrl] = useState("");
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [discoveryUrl, setDiscoveryUrl] = useState("");
  const [jitMode, setJitMode] = useState<JitProvisioningMode>("CAPPED");
  const [seatCap, setSeatCap] = useState<number | "">(25);

  const create = useMutation({
    mutationFn: () => {
      const payload: IdpConfigCreate = {
        verified_domain_id: domainId,
        protocol,
        display_name: displayName.trim(),
        jit_provisioning_mode: jitMode,
        jit_default_org_role: "MEMBER",
        jit_seat_cap: jitMode === "CAPPED" && seatCap !== "" ? seatCap : null,
        ...(protocol === "SAML2"
          ? {
              metadata_url: metadataUrl.trim() || null,
              idp_entity_id: entityId.trim() || null,
              idp_sso_url: ssoUrl.trim() || null,
            }
          : {
              oidc_issuer: issuer.trim() || null,
              oidc_client_id: clientId.trim() || null,
              oidc_client_secret: clientSecret.trim() || null,
              oidc_discovery_url: discoveryUrl.trim() || null,
            }),
      };
      return createIdpConfig(organizationId, payload);
    },
    onSuccess: (config) => onCreated(config.id),
  });

  const valid =
    displayName.trim().length > 0 &&
    domainId.length > 0 &&
    (protocol === "SAML2"
      ? metadataUrl.trim().length > 0 ||
        (entityId.trim().length > 0 && ssoUrl.trim().length > 0)
      : issuer.trim().length > 0 && clientId.trim().length > 0);

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-medium">New connection</h3>

      <div role="group" aria-label="Protocol" className="flex gap-1">
        {(["SAML2", "OIDC"] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setProtocol(option)}
            aria-pressed={protocol === option}
            className={[
              "rounded border px-3 py-1 text-xs",
              protocol === option
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:bg-muted",
            ].join(" ")}
          >
            {option === "SAML2" ? "SAML 2.0" : "OIDC"}
          </button>
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium">Name</span>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Okta"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>

        <label className="block text-sm">
          <span className="font-medium">Verified domain</span>
          <select
            value={domainId}
            onChange={(e) => setDomainId(e.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            {domains.map((domain) => (
              <option key={domain.id} value={domain.id}>
                {domain.domain}
              </option>
            ))}
          </select>
        </label>
      </div>

      {protocol === "SAML2" ? (
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="font-medium">Metadata URL</span>
            <input
              value={metadataUrl}
              onChange={(e) => setMetadataUrl(e.target.value)}
              placeholder="https://idp.example.com/app/metadata"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
            <span className="mt-1 block text-xs text-muted-foreground">
              Easiest path — the server fetches and parses it. Or enter the two
              fields below by hand.
            </span>
          </label>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="font-medium">Entity ID</span>
              <input
                value={entityId}
                onChange={(e) => setEntityId(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="font-medium">SSO URL</span>
              <input
                value={ssoUrl}
                onChange={(e) => setSsoUrl(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="font-medium">Issuer</span>
              <input
                value={issuer}
                onChange={(e) => setIssuer(e.target.value)}
                placeholder="https://idp.example.com"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="font-medium">Discovery URL</span>
              <input
                value={discoveryUrl}
                onChange={(e) => setDiscoveryUrl(e.target.value)}
                placeholder=".well-known/openid-configuration"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="font-medium">Client ID</span>
              <input
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="font-medium">Client secret</span>
              <input
                type="password"
                autoComplete="off"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>
        </div>
      )}

      <div className="rounded-md border border-border bg-muted/30 p-3">
        <span className="text-sm font-medium">Provisioning</span>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {(["CAPPED", "INVITE_ONLY", "OPEN"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setJitMode(option)}
              aria-pressed={jitMode === option}
              className={[
                "rounded border px-2 py-1 text-xs",
                jitMode === option
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:bg-background",
              ].join(" ")}
            >
              {option === "INVITE_ONLY" ? "Invite only" : option.toLowerCase()}
            </button>
          ))}
        </div>

        {jitMode === "CAPPED" && (
          <label className="mt-2 block text-sm">
            <span className="text-xs font-medium">Seat cap</span>
            <input
              type="number"
              min={0}
              value={seatCap}
              onChange={(e) =>
                setSeatCap(e.target.value === "" ? "" : Number(e.target.value))
              }
              className="mt-1 w-28 rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
          </label>
        )}

        {jitMode === "OPEN" && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-destructive">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Every person who signs in becomes an active member and adds a
            billable seat, prorated immediately, with no ceiling. Your identity
            provider controls who and when.
          </p>
        )}
      </div>

      {create.isError && (
        <p role="alert" className="text-sm text-destructive">
          The connection couldn&apos;t be created. Check the metadata URL or
          issuer.
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={create.isPending}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={!valid || create.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Create
        </button>
      </div>
    </div>
  );
};

const CertificatePanel: React.FC<{
  organizationId: string;
  configId: string;
  canEdit: boolean;
}> = ({ organizationId, configId, canEdit }) => {
  const [pem, setPem] = useState("");
  const [adding, setAdding] = useState(false);

  const add = useMutation({
    mutationFn: () =>
      addCertificate(organizationId, configId, {
        certificate_pem: pem.trim(),
        side: "IDP",
        is_primary: false,
      }),
    onSuccess: () => {
      setPem("");
      setAdding(false);
    },
  });

  return (
    <section>
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-medium">Signing certificates</h4>
        {canEdit && !adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded border border-border px-2 py-0.5 text-xs hover:bg-muted"
          >
            Add
          </button>
        )}
      </div>

      <p className="mt-1 text-xs text-muted-foreground">
        Add the new certificate before your provider starts using it. Both are
        trusted during the overlap, so rotation causes no downtime.
      </p>

      {adding && (
        <div className="mt-2 space-y-2">
          <textarea
            value={pem}
            onChange={(e) => setPem(e.target.value)}
            rows={4}
            placeholder="-----BEGIN CERTIFICATE-----"
            aria-label="Certificate PEM"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setAdding(false);
                setPem("");
              }}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-muted"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => add.mutate()}
              disabled={!pem.trim().startsWith("-----BEGIN") || add.isPending}
              className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"
            >
              {add.isPending ? "Adding…" : "Add certificate"}
            </button>
          </div>
          {add.isError && (
            <p role="alert" className="text-xs text-destructive">
              That certificate wasn&apos;t accepted. Check it is a PEM-encoded
              X.509 certificate.
            </p>
          )}
        </div>
      )}
    </section>
  );
};

const RoleMappingPanel: React.FC<{
  organizationId: string;
  configId: string;
  canEdit: boolean;
}> = ({ organizationId, configId, canEdit }) => {
  const [attributeName, setAttributeName] = useState("groups");
  const [matchKind, setMatchKind] = useState<RoleMatchKind>("EQUALS");
  const [matchValue, setMatchValue] = useState("");
  const [role, setRole] = useState<OrganizationRoleName>("MEMBER");
  const [testValue, setTestValue] = useState("");
  const [result, setResult] = useState<DryRunResult | null>(null);

  const save = useMutation({
    mutationFn: () =>
      addRoleMapping(organizationId, configId, {
        attribute_name: attributeName.trim(),
        match_kind: matchKind,
        match_value: matchValue.trim(),
        organization_role: role,
        priority: 100,
      }),
  });

  const dryRun = useMutation({
    mutationFn: () =>
      dryRunRoleMapping(organizationId, configId, {
        attributes: {
          [attributeName.trim()]: testValue
            .split(",")
            .map((entry) => entry.trim())
            .filter(Boolean),
        },
      }),
    onSuccess: setResult,
  });

  return (
    <section>
      <h4 className="text-xs font-medium">Role mapping</h4>
      <p className="mt-1 text-xs text-muted-foreground">
        Map an assertion attribute to an organization role.
      </p>

      <div className="mt-2 grid gap-2 sm:grid-cols-4">
        <input
          value={attributeName}
          onChange={(e) => setAttributeName(e.target.value)}
          placeholder="attribute"
          aria-label="Attribute name"
          className="rounded border border-border bg-background px-2 py-1.5 text-xs"
        />
        <select
          value={matchKind}
          onChange={(e) => setMatchKind(e.target.value as RoleMatchKind)}
          aria-label="Match kind"
          className="rounded border border-border bg-background px-2 py-1.5 text-xs"
        >
          {MATCH_KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {kind.toLowerCase()}
            </option>
          ))}
        </select>
        <input
          value={matchValue}
          onChange={(e) => setMatchValue(e.target.value)}
          placeholder="value"
          aria-label="Match value"
          className="rounded border border-border bg-background px-2 py-1.5 text-xs"
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as OrganizationRoleName)}
          aria-label="Organization role"
          className="rounded border border-border bg-background px-2 py-1.5 text-xs"
        >
          {ROLES.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-3 rounded-md border border-border bg-muted/30 p-2.5">
        <p className="text-xs font-medium">Test it</p>
        <div className="mt-1.5 flex flex-wrap gap-2">
          <input
            value={testValue}
            onChange={(e) => setTestValue(e.target.value)}
            placeholder="engineering, admins"
            aria-label="Sample attribute values"
            className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1.5 text-xs"
          />
          <button
            type="button"
            onClick={() => dryRun.mutate()}
            disabled={dryRun.isPending || testValue.trim().length === 0}
            className="inline-flex items-center gap-1.5 rounded border border-border bg-background px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
          >
            {dryRun.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            Dry run
          </button>
        </div>

        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Provisions nobody and costs nothing.
        </p>

        {result && (
          <dl className="mt-2 space-y-1 rounded border border-border bg-background p-2 text-xs">
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Resolved role</dt>
              <dd className="font-medium">{result.resolved_role}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Seats now</dt>
              <dd>
                {result.current_seats}
                {result.seat_cap !== null && ` of ${result.seat_cap}`}
              </dd>
            </div>
            <div
              className={
                result.would_consume_seat
                  ? "flex gap-2 font-medium text-amber-700"
                  : "flex gap-2"
              }
            >
              <dt>Would add a billable seat</dt>
              <dd>{result.would_consume_seat ? "yes" : "no"}</dd>
            </div>
          </dl>
        )}
      </div>

      {canEdit && (
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={matchValue.trim().length === 0 || save.isPending}
            className="rounded bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save mapping"}
          </button>
        </div>
      )}
    </section>
  );
};

export default IdpConnectionBuilder;
