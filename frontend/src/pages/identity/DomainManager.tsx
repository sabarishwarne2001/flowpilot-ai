import React, { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Globe, Loader2, RefreshCw, ShieldCheck } from "lucide-react";

import {
  bindDomainSso,
  claimDomain,
  listDomains,
  verifyDomain,
} from "@/services/api/identity";
import { identityKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import type { DomainRead } from "@/types/identity";

interface RegistrarHint {
  readonly name: string;
  readonly nameField: string;
  readonly nameValue: (domain: string) => string;
  readonly note: string;
}

const REGISTRARS: readonly RegistrarHint[] = [
  {
    name: "Cloudflare",
    nameField: "Name",
    nameValue: () => "@",
    note: "Set Proxy status to DNS only. TTL Auto is fine.",
  },
  {
    name: "AWS Route 53",
    nameField: "Record name",
    nameValue: (domain) => domain,
    note: 'Route 53 requires the value wrapped in double quotes.',
  },
  {
    name: "GoDaddy",
    nameField: "Host",
    nameValue: () => "@",
    note: "Leave TTL at 1 hour. Do not add a trailing dot.",
  },
  {
    name: "Namecheap",
    nameField: "Host",
    nameValue: () => "@",
    note: "Use Advanced DNS → Add New Record → TXT Record.",
  },
];

interface StatusPill {
  readonly label: string;
  readonly classes: string;
  readonly consequence: string;
}

const statusPill = (domain: DomainRead): StatusPill => {
  const status = String(domain.status).toUpperCase();

  if (status === "VERIFIED") {
    return {
      label: "Verified",
      classes: "bg-emerald-500/15 text-emerald-700",
      consequence: domain.is_sso_binding
        ? "Users with this email domain sign in through your identity provider."
        : "Ready to bind to an identity provider.",
    };
  }

  if (status === "LAPSED") {
    return {
      label: "DNS record missing",
      classes: "bg-amber-500/20 text-amber-800",
      consequence:
        "Existing users are unaffected and can still sign in. New accounts cannot be provisioned until the TXT record is restored.",
    };
  }

  if (status === "REVOKED") {
    return {
      label: "Revoked",
      classes: "bg-destructive/15 text-destructive",
      consequence: "This domain no longer grants anything.",
    };
  }

  return {
    label: "Awaiting DNS",
    classes: "bg-muted text-muted-foreground",
    consequence:
      "Publish the TXT record below, then check. DNS can take up to 48 hours to propagate.",
  };
};

const CopyButton: React.FC<{ value: string; label: string }> = ({
  value,
  label,
}) => {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Ignored
    }
  }, [value]);

  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={`Copy ${label}`}
      className="shrink-0 rounded border border-border p-1 hover:bg-muted"
    >
      {copied ? (
        <Check className="h-3 w-3 text-emerald-600" />
      ) : (
        <Copy className="h-3 w-3" />
      )}
    </button>
  );
};

export const DomainManager: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const queryClient = useQueryClient();
  const isOwner = String(organizationRole).toUpperCase() === "OWNER";

  const [newDomain, setNewDomain] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [registrar, setRegistrar] = useState(0);

  const domainsQuery = useQuery({
    queryKey: identityKeys.domains(organizationId),
    queryFn: () => listDomains(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const invalidate = useCallback(
    () =>
      queryClient.invalidateQueries({
        queryKey: identityKeys.domains(organizationId),
      }),
    [queryClient, organizationId],
  );

  const claim = useMutation({
    mutationFn: () => claimDomain(organizationId, { domain: newDomain.trim() }),
    onSuccess: async (created) => {
      setNewDomain("");
      setExpanded(created.id);
      await invalidate();
    },
  });

  const verify = useMutation({
    mutationFn: (domainId: string) => verifyDomain(organizationId, domainId),
    onSuccess: invalidate,
  });

  const bind = useMutation({
    mutationFn: (domainId: string) => bindDomainSso(organizationId, domainId),
    onSuccess: invalidate,
  });

  const domains = domainsQuery.data ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">Domains</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Prove you control a domain before binding it to single sign-on.
        </p>
      </div>

      {isOwner && (
        <div className="flex flex-wrap gap-2">
          <input
            value={newDomain}
            onChange={(e) => setNewDomain(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newDomain.trim()) {
                e.preventDefault();
                claim.mutate();
              }
            }}
            placeholder="example.com"
            aria-label="Domain to claim"
            className="min-w-[16rem] flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="button"
            onClick={() => claim.mutate()}
            disabled={claim.isPending || newDomain.trim().length < 4}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {claim.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Claim domain
          </button>
        </div>
      )}

      {claim.isError && (
        <p role="alert" className="text-sm text-destructive">
          That domain couldn&apos;t be claimed. It may already be registered to
          another organization.
        </p>
      )}

      {domainsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading domains…</p>
      ) : domains.length === 0 ? (
        <p className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
          No domains claimed yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {domains.map((domain) => {
            const pill = statusPill(domain);
            const isOpen = expanded === domain.id;
            const hint = REGISTRARS[registrar] ?? REGISTRARS[0];

            return (
              <li
                key={domain.id}
                className="rounded-lg border border-border bg-card"
              >
                <div className="flex flex-wrap items-center gap-3 p-3">
                  <Globe
                    className="h-4 w-4 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {domain.domain}
                      </span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${pill.classes}`}
                      >
                        {pill.label}
                      </span>
                      {domain.is_sso_binding && (
                        <span className="inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[11px] text-primary">
                          <ShieldCheck className="h-3 w-3" />
                          SSO
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {pill.consequence}
                    </p>
                  </div>

                  <div className="flex shrink-0 gap-2">
                    {isOwner && String(domain.status).toUpperCase() !== "VERIFIED" && (
                      <button
                        type="button"
                        onClick={() => verify.mutate(domain.id)}
                        disabled={verify.isPending}
                        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
                      >
                        {verify.isPending ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="h-3.5 w-3.5" />
                        )}
                        Check DNS
                      </button>
                    )}

                    {isOwner &&
                      String(domain.status).toUpperCase() === "VERIFIED" &&
                      !domain.is_sso_binding && (
                        <button
                          type="button"
                          onClick={() => bind.mutate(domain.id)}
                          disabled={bind.isPending}
                          className="rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
                        >
                          Bind to SSO
                        </button>
                      )}

                    <button
                      type="button"
                      onClick={() => setExpanded(isOpen ? null : domain.id)}
                      aria-expanded={isOpen}
                      className="rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted"
                    >
                      {isOpen ? "Hide" : "DNS record"}
                    </button>
                  </div>
                </div>

                {isOpen && (
                  <div className="border-t border-border bg-muted/20 p-3">
                    <div
                      role="group"
                      aria-label="Registrar"
                      className="flex flex-wrap gap-1"
                    >
                      {REGISTRARS.map((option, index) => (
                        <button
                          key={option.name}
                          type="button"
                          onClick={() => setRegistrar(index)}
                          aria-pressed={registrar === index}
                          className={[
                            "rounded border px-2 py-0.5 text-xs",
                            registrar === index
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border text-muted-foreground hover:bg-background",
                          ].join(" ")}
                        >
                          {option.name}
                        </button>
                      ))}
                    </div>

                    <dl className="mt-3 space-y-2">
                      <div>
                        <dt className="text-[11px] text-muted-foreground">
                          Type
                        </dt>
                        <dd className="font-mono text-xs">TXT</dd>
                      </div>

                      <div>
                        <dt className="text-[11px] text-muted-foreground">
                          {hint?.nameField ?? "Name"}
                        </dt>
                        <dd className="flex items-center gap-2">
                          <span className="font-mono text-xs">
                            {hint?.nameValue(domain.domain) ?? "@"}
                          </span>
                          <CopyButton
                            value={hint?.nameValue(domain.domain) ?? "@"}
                            label="record name"
                          />
                        </dd>
                      </div>

                      <div>
                        <dt className="text-[11px] text-muted-foreground">
                          Value
                        </dt>
                        <dd className="flex items-start gap-2">
                          <span className="min-w-0 flex-1 break-all rounded bg-background px-2 py-1 font-mono text-xs">
                            {domain.expected_txt_record}
                          </span>
                          <CopyButton
                            value={domain.expected_txt_record}
                            label="record value"
                          />
                        </dd>
                      </div>
                    </dl>

                    {hint?.note && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        {hint.note}
                      </p>
                    )}

                    <div className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">
                      {domain.challenge_expires_at && (
                        <p>
                          Challenge expires{" "}
                          {new Date(
                            domain.challenge_expires_at,
                          ).toLocaleString()}
                        </p>
                      )}
                      {domain.last_checked_at && (
                        <p>
                          Last checked{" "}
                          {new Date(domain.last_checked_at).toLocaleString()}
                        </p>
                      )}
                      {domain.grace_expires_at && (
                        <p>
                          Grace period ends{" "}
                          {new Date(domain.grace_expires_at).toLocaleString()}
                        </p>
                      )}
                      <p>
                        New account provisioning:{" "}
                        {domain.provisioning_allowed ? "allowed" : "blocked"}
                      </p>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {!isOwner && (
        <p className="text-xs text-muted-foreground">
          Only an organization owner can claim or verify domains.
        </p>
      )}
    </div>
  );
};

export default DomainManager;
