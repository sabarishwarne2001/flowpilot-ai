import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  Globe,
  Loader2,
  Palette,
  RefreshCw,
  ShieldCheck,
  Star,
  Trash2,
  Upload,
} from "lucide-react";

import {
  claimCustomDomain,
  clearFavicon,
  clearLogo,
  getBranding,
  listCustomDomains,
  reissueChallenge,
  releaseCustomDomain,
  requestCertificate,
  revokeCustomDomain,
  setPrimaryDomain,
  setSenderDomain,
  updateBranding,
  uploadFavicon,
  uploadLogo,
  verifyCustomDomain,
  verifySenderDomain,
} from "@/services/api/branding";
import { brandingKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  CERTIFICATE_STATUS_LABELS,
  DOMAIN_STATUS_CLASSES,
  DOMAIN_STATUS_LABELS,
  SENDER_STATUS_LABELS,
  formatDateTime,
  isValidHexColor,
  type ColorScheme,
  type CustomDomainDetail,
  type TenantBrandingResponse,
  type TenantBrandingUpdate,
} from "@/types/branding";

const CARD =
  "rounded-lg border border-border bg-card p-5 text-card-foreground shadow-sm";
const LABEL = "text-sm font-medium text-foreground";
const HINT = "text-xs text-muted-foreground";
const INPUT =
  "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";
const BUTTON =
  "inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed";
const PRIMARY = `${BUTTON} bg-primary text-primary-foreground hover:opacity-90`;
const SECONDARY = `${BUTTON} border border-border bg-background hover:bg-muted`;
const DANGER = `${BUTTON} border border-red-200 text-red-700 hover:bg-red-50`;

const COLOR_FIELDS = [
  { key: "primary_color", label: "Primary" },
  { key: "accent_color", label: "Accent" },
  { key: "background_color", label: "Background" },
  { key: "foreground_color", label: "Text" },
] as const;

type ColorKey = (typeof COLOR_FIELDS)[number]["key"];

const SCHEMES: readonly ColorScheme[] = ["SYSTEM", "LIGHT", "DARK"];

const errorMessage = (error: unknown): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    return first?.msg ?? "The request was rejected.";
  }
  return "Something went wrong. Please try again.";
};

const CopyButton: React.FC<{ value: string; label: string }> = ({
  value,
  label,
}) => {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={SECONDARY}
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
      aria-label={`Copy ${label}`}
    >
      <Copy className="h-3.5 w-3.5" aria-hidden />
      {copied ? "Copied" : "Copy"}
    </button>
  );
};

// ---------------------------------------------------------------------------
// DNS challenge
// ---------------------------------------------------------------------------

const ChallengeInstructions: React.FC<{ domain: CustomDomainDetail }> = ({
  domain,
}) => (
  <div className="mt-3 rounded-md border border-border bg-muted/40 p-3">
    <p className={LABEL}>Publish this TXT record, then verify</p>
    <p className={`${HINT} mt-1`}>
      DNS changes can take up to 48 hours to propagate. Verification is also
      retried automatically in the background.
    </p>
    <dl className="mt-3 space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <dt className="w-20 text-muted-foreground">Name</dt>
        <dd className="font-mono text-xs break-all">
          {domain.challenge.record_name}
        </dd>
        <CopyButton
          value={domain.challenge.record_name}
          label="record name"
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <dt className="w-20 text-muted-foreground">Type</dt>
        <dd className="font-mono text-xs">{domain.challenge.record_type}</dd>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <dt className="w-20 text-muted-foreground">Value</dt>
        <dd className="font-mono text-xs break-all">
          {domain.challenge.record_value}
        </dd>
        <CopyButton
          value={domain.challenge.record_value}
          label="record value"
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <dt className="w-20 text-muted-foreground">Expires</dt>
        <dd className="text-xs">
          {formatDateTime(domain.challenge.expires_at)}
        </dd>
      </div>
    </dl>
  </div>
);

// ---------------------------------------------------------------------------
// One domain row
// ---------------------------------------------------------------------------

interface DomainRowProps {
  readonly domain: CustomDomainDetail;
  readonly isOwner: boolean;
  readonly busy: boolean;
  readonly onVerify: (id: string) => void;
  readonly onReissue: (id: string) => void;
  readonly onPrimary: (id: string, next: boolean) => void;
  readonly onCertificate: (id: string) => void;
  readonly onRevoke: (id: string) => void;
  readonly onRelease: (id: string) => void;
}

const DomainRow: React.FC<DomainRowProps> = ({
  domain,
  isOwner,
  busy,
  onVerify,
  onReissue,
  onPrimary,
  onCertificate,
  onRevoke,
  onRelease,
}) => (
  <li className="rounded-md border border-border p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2">
        <Globe className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="font-medium">{domain.hostname}</span>
        {domain.is_primary ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
            <Star className="h-3 w-3" aria-hidden />
            Primary
          </span>
        ) : null}
      </div>
      <span
        className={`rounded-full px-2 py-0.5 text-xs ${DOMAIN_STATUS_CLASSES[domain.status]}`}
      >
        {DOMAIN_STATUS_LABELS[domain.status]}
      </span>
    </div>

    <p className={`${HINT} mt-2`}>
      TLS: {CERTIFICATE_STATUS_LABELS[domain.certificate_status]}
      {domain.certificate_expires_at
        ? ` · expires ${formatDateTime(domain.certificate_expires_at)}`
        : ""}
      {domain.last_checked_at
        ? ` · last checked ${formatDateTime(domain.last_checked_at)}`
        : ""}
    </p>

    {domain.last_failure_reason ? (
      <p className="mt-2 flex items-start gap-2 text-xs text-amber-700">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        {domain.last_failure_reason}
      </p>
    ) : null}

    {domain.certificate_last_error ? (
      <p className="mt-2 flex items-start gap-2 text-xs text-red-700">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        {domain.certificate_last_error}
      </p>
    ) : null}

    {domain.status !== "VERIFIED" ? (
      <ChallengeInstructions domain={domain} />
    ) : null}

    {isOwner ? (
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className={SECONDARY}
          disabled={busy}
          onClick={() => onVerify(domain.id)}
        >
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
          Verify now
        </button>
        <button
          type="button"
          className={SECONDARY}
          disabled={busy}
          onClick={() => onReissue(domain.id)}
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          New challenge
        </button>
        <button
          type="button"
          className={PRIMARY}
          /*
           * The server's flag, not a local derivation. See CustomDomainDetail
           * in @/types/branding: enabling this from status would show an
           * enabled button the API then refuses.
           */
          disabled={busy || !domain.may_request_certificate}
          onClick={() => onCertificate(domain.id)}
        >
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
          {domain.certificate_status === "NONE"
            ? "Request certificate"
            : "Renew certificate"}
        </button>
        {domain.status === "VERIFIED" && !domain.is_primary ? (
          <button
            type="button"
            className={SECONDARY}
            disabled={busy}
            onClick={() => onPrimary(domain.id, true)}
          >
            <Star className="h-3.5 w-3.5" aria-hidden />
            Make primary
          </button>
        ) : null}
        {domain.status !== "REVOKED" ? (
          <button
            type="button"
            className={DANGER}
            disabled={busy}
            onClick={() => onRevoke(domain.id)}
          >
            Stop serving
          </button>
        ) : null}
        <button
          type="button"
          className={DANGER}
          disabled={busy}
          onClick={() => onRelease(domain.id)}
          title="Deletes the claim. Another organization could then take this hostname."
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
          Release
        </button>
      </div>
    ) : (
      <p className={`${HINT} mt-3`}>
        Domain changes are restricted to the organization owner.
      </p>
    )}
  </li>
);

// ---------------------------------------------------------------------------
// Theme preview
// ---------------------------------------------------------------------------

const ThemePreview: React.FC<{
  readonly draft: Record<ColorKey, string>;
  readonly brandName: string;
  readonly logoUrl: string | null;
}> = ({ draft, brandName, logoUrl }) => {
  const background = isValidHexColor(draft.background_color)
    ? draft.background_color
    : "#ffffff";
  const foreground = isValidHexColor(draft.foreground_color)
    ? draft.foreground_color
    : "#111827";
  const primary = isValidHexColor(draft.primary_color)
    ? draft.primary_color
    : "#2563eb";
  const accent = isValidHexColor(draft.accent_color)
    ? draft.accent_color
    : "#7c3aed";

  return (
    <div
      className="rounded-md border border-border p-6"
      /*
       * Inline style with four values that have already passed the hex test.
       * Not dangerouslySetInnerHTML, not an injected <style> block, and no
       * string concatenation into CSS: the only thing crossing this boundary
       * is a validated six-digit token, so there is nothing here a tenant
       * could turn into script. verify_arch25.py G17 fails if that changes.
       */
      style={{ backgroundColor: background, color: foreground }}
    >
      <div className="flex items-center gap-3">
        {logoUrl ? (
          <img
            src={logoUrl}
            alt=""
            className="h-8 w-auto"
            style={{ maxWidth: 160 }}
          />
        ) : null}
        <span className="text-lg font-semibold">
          {brandName.trim() || "Your brand"}
        </span>
      </div>
      <p className="mt-3 text-sm opacity-80">
        Sign in to continue to your workspace.
      </p>
      <div className="mt-4 flex gap-2">
        <span
          className="rounded-md px-3 py-2 text-sm font-medium"
          style={{ backgroundColor: primary, color: background }}
        >
          Sign in
        </span>
        <span
          className="rounded-md px-3 py-2 text-sm font-medium"
          style={{ backgroundColor: accent, color: background }}
        >
          Single sign-on
        </span>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const OrganizationBranding: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const queryClient = useQueryClient();
  const isOwner = String(organizationRole).toUpperCase() === "OWNER";

  const [hostname, setHostname] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [senderDraft, setSenderDraft] = useState<string | null>(null);
  const [brandDraft, setBrandDraft] = useState<string | null>(null);
  const [colorDraft, setColorDraft] = useState<Partial<Record<ColorKey, string>>>(
    {},
  );

  const domainsQuery = useQuery({
    queryKey: brandingKeys.domains(organizationId),
    queryFn: () => listCustomDomains(organizationId),
  });

  const brandingQuery = useQuery({
    queryKey: brandingKeys.branding(organizationId),
    queryFn: () => getBranding(organizationId),
  });

  const branding: TenantBrandingResponse | undefined = brandingQuery.data;

  const colors = useMemo(() => {
    const resolved = {} as Record<ColorKey, string>;
    for (const field of COLOR_FIELDS) {
      resolved[field.key] =
        colorDraft[field.key] ?? branding?.[field.key] ?? "";
    }
    return resolved;
  }, [branding, colorDraft]);

  const brandName = brandDraft ?? branding?.brand_name ?? "";

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: brandingKeys.all(organizationId),
    });
  };

  const report = (message: string) => {
    setNotice(message);
    setProblem(null);
  };

  const fail = (error: unknown) => {
    setProblem(errorMessage(error));
    setNotice(null);
  };

  const claim = useMutation({
    mutationFn: () => claimCustomDomain(organizationId, { hostname }),
    onSuccess: (created) => {
      setHostname("");
      report(
        `${created.hostname} claimed. Publish the TXT record below, then verify.`,
      );
      refresh();
    },
    onError: fail,
  });

  const verify = useMutation({
    mutationFn: (id: string) => verifyCustomDomain(organizationId, id),
    onSuccess: (result) => {
      // resolver_failed is reported differently from a missing record. Our DNS
      // outage is not the customer's problem and must not read as one.
      report(
        result.verified
          ? `${result.hostname} is verified.`
          : result.resolver_failed
            ? "We could not reach a DNS resolver. That is on our side — try again shortly."
            : result.detail,
      );
      refresh();
    },
    onError: fail,
  });

  const reissue = useMutation({
    mutationFn: (id: string) => reissueChallenge(organizationId, id),
    onSuccess: () => {
      report("A new challenge token was issued. Update your TXT record.");
      refresh();
    },
    onError: fail,
  });

  const primary = useMutation({
    mutationFn: ({ id, next }: { id: string; next: boolean }) =>
      setPrimaryDomain(organizationId, id, next),
    onSuccess: () => {
      report("Primary hostname updated.");
      refresh();
    },
    onError: fail,
  });

  const certificate = useMutation({
    mutationFn: (id: string) => requestCertificate(organizationId, id),
    onSuccess: () => {
      report("Certificate issuance requested.");
      refresh();
    },
    onError: fail,
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeCustomDomain(organizationId, id),
    onSuccess: () => {
      report("This hostname is no longer served. The claim is retained.");
      refresh();
    },
    onError: fail,
  });

  const release = useMutation({
    mutationFn: (id: string) => releaseCustomDomain(organizationId, id),
    onSuccess: () => {
      report("Hostname released. Another organization can now claim it.");
      refresh();
    },
    onError: fail,
  });

  const saveTokens = useMutation({
    mutationFn: (payload: TenantBrandingUpdate) =>
      updateBranding(organizationId, payload),
    onSuccess: () => {
      setBrandDraft(null);
      setColorDraft({});
      report("Branding saved.");
      refresh();
    },
    onError: fail,
  });

  const logoUpload = useMutation({
    mutationFn: (file: File) => uploadLogo(organizationId, file),
    onSuccess: () => {
      report("Logo updated.");
      refresh();
    },
    onError: fail,
  });

  const faviconUpload = useMutation({
    mutationFn: (file: File) => uploadFavicon(organizationId, file),
    onSuccess: () => {
      report("Favicon updated.");
      refresh();
    },
    onError: fail,
  });

  const logoClear = useMutation({
    mutationFn: () => clearLogo(organizationId),
    onSuccess: () => {
      report("Logo removed.");
      refresh();
    },
    onError: fail,
  });

  const faviconClear = useMutation({
    mutationFn: () => clearFavicon(organizationId),
    onSuccess: () => {
      report("Favicon removed.");
      refresh();
    },
    onError: fail,
  });

  const saveSender = useMutation({
    mutationFn: (value: string | null) =>
      setSenderDomain(organizationId, { sender_domain: value }),
    onSuccess: () => {
      setSenderDraft(null);
      report("Sender domain saved. Publish the records, then verify.");
      refresh();
    },
    onError: fail,
  });

  const checkSender = useMutation({
    mutationFn: () => verifySenderDomain(organizationId),
    onSuccess: (result) => {
      report(
        result.may_send_as_tenant
          ? "Sender domain verified. Mail will now be sent from your domain."
          : (result.degradation_reason ??
            "The sender domain has not verified yet."),
      );
      refresh();
    },
    onError: fail,
  });

  const busy =
    claim.isPending ||
    verify.isPending ||
    reissue.isPending ||
    primary.isPending ||
    certificate.isPending ||
    revoke.isPending ||
    release.isPending;

  const dirtyColors = COLOR_FIELDS.filter(
    (field) => colorDraft[field.key] !== undefined,
  );
  const invalidColors = dirtyColors.filter(
    (field) =>
      (colors[field.key] ?? "") !== "" && !isValidHexColor(colors[field.key]),
  );

  const submitTokens = () => {
    const payload: TenantBrandingUpdate = {};
    if (brandDraft !== null) {
      payload.brand_name = brandDraft.trim() === "" ? null : brandDraft.trim();
    }
    for (const field of dirtyColors) {
      const value = colors[field.key]?.trim() ?? "";
      payload[field.key] = value === "" ? null : value.toLowerCase();
    }
    saveTokens.mutate(payload);
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <Palette className="h-5 w-5 text-muted-foreground" aria-hidden />
          Branding &amp; custom domains
        </h1>
        <p className={`${HINT} mt-1`}>
          Reach FlowPilot at your own hostname, show your own brand, and send
          notifications from your own domain.
        </p>
      </header>

      {notice ? (
        <div className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {notice}
        </div>
      ) : null}
      {problem ? (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {problem}
        </div>
      ) : null}

      {/* ---- Custom domains ---- */}
      <section className={CARD}>
        <h2 className="text-base font-semibold">Custom domains</h2>
        <p className={`${HINT} mt-1`}>
          A hostname resolves to exactly one organization, so we verify you
          control the DNS zone before serving it. A certificate is only ever
          requested after that verification succeeds.
        </p>

        {isOwner ? (
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <div className="min-w-64 flex-1">
              <label className={LABEL} htmlFor="hostname">
                Hostname
              </label>
              <input
                id="hostname"
                className={INPUT}
                placeholder="ai.acme.com"
                value={hostname}
                onChange={(event) => setHostname(event.target.value)}
                autoComplete="off"
              />
            </div>
            <button
              type="button"
              className={PRIMARY}
              disabled={!hostname.trim() || claim.isPending}
              onClick={() => claim.mutate()}
            >
              {claim.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Globe className="h-4 w-4" aria-hidden />
              )}
              Claim domain
            </button>
          </div>
        ) : null}

        <div className="mt-4">
          {domainsQuery.isLoading ? (
            <p className={HINT}>Loading domains…</p>
          ) : domainsQuery.data && domainsQuery.data.length > 0 ? (
            <ul className="space-y-3">
              {domainsQuery.data.map((domain) => (
                <DomainRow
                  key={domain.id}
                  domain={domain}
                  isOwner={isOwner}
                  busy={busy}
                  onVerify={(id) => verify.mutate(id)}
                  onReissue={(id) => reissue.mutate(id)}
                  onPrimary={(id, next) => primary.mutate({ id, next })}
                  onCertificate={(id) => certificate.mutate(id)}
                  onRevoke={(id) => revoke.mutate(id)}
                  onRelease={(id) => release.mutate(id)}
                />
              ))}
            </ul>
          ) : (
            <p className={HINT}>No custom domains yet.</p>
          )}
        </div>
      </section>

      {/* ---- Brand tokens ---- */}
      <section className={CARD}>
        <h2 className="text-base font-semibold">Brand</h2>
        <p className={`${HINT} mt-1`}>
          Colours are six-digit hex values. Named colours, rgb(), CSS variables
          and stylesheet fragments are not accepted — branding is a fixed set of
          tokens rather than stylesheet input, because these pages are served
          from an origin shared with other tenants.
        </p>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <label className={LABEL} htmlFor="brand-name">
              Brand name
            </label>
            <input
              id="brand-name"
              className={INPUT}
              value={brandName}
              maxLength={120}
              onChange={(event) => setBrandDraft(event.target.value)}
              placeholder="Acme Inc."
            />
            <p className={`${HINT} mt-1`}>
              Ampersands and apostrophes are fine. Angle brackets, double
              quotes and backslashes are not.
            </p>

            <div className="mt-4 grid grid-cols-2 gap-3">
              {COLOR_FIELDS.map((field) => {
                const value = colors[field.key] ?? "";
                const valid = value === "" || isValidHexColor(value);
                return (
                  <div key={field.key}>
                    <label className={LABEL} htmlFor={`color-${field.key}`}>
                      {field.label}
                    </label>
                    <div className="mt-1 flex items-center gap-2">
                      <input
                        id={`color-${field.key}`}
                        className={`${INPUT} mt-0 font-mono`}
                        value={value}
                        placeholder="#1a73e8"
                        onChange={(event) =>
                          setColorDraft((current) => ({
                            ...current,
                            [field.key]: event.target.value,
                          }))
                        }
                      />
                      <span
                        aria-hidden
                        className="h-8 w-8 shrink-0 rounded border border-border"
                        style={{
                          backgroundColor: valid && value ? value : undefined,
                        }}
                      />
                    </div>
                    {!valid ? (
                      <p className="mt-1 text-xs text-red-700">
                        Use six-digit hex, e.g. #1a73e8.
                      </p>
                    ) : null}
                  </div>
                );
              })}
            </div>

            <div className="mt-4">
              <span className={LABEL}>Colour scheme</span>
              <div className="mt-1 flex gap-2">
                {SCHEMES.map((scheme) => (
                  <button
                    key={scheme}
                    type="button"
                    className={
                      branding?.color_scheme === scheme ? PRIMARY : SECONDARY
                    }
                    onClick={() => saveTokens.mutate({ color_scheme: scheme })}
                  >
                    {scheme.charAt(0) + scheme.slice(1).toLowerCase()}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                className={PRIMARY}
                disabled={
                  saveTokens.isPending ||
                  invalidColors.length > 0 ||
                  (brandDraft === null && dirtyColors.length === 0)
                }
                onClick={submitTokens}
              >
                {saveTokens.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : null}
                Save brand
              </button>
              <button
                type="button"
                className={SECONDARY}
                onClick={() =>
                  saveTokens.mutate({ is_enabled: !branding?.is_enabled })
                }
              >
                {branding?.is_enabled
                  ? "Disable custom branding"
                  : "Enable custom branding"}
              </button>
            </div>
            <p className={`${HINT} mt-2`}>
              {branding?.is_enabled
                ? "Custom branding is live on your verified domains."
                : "Saved but not applied. Nothing a visitor sees changes until you enable it."}
            </p>
          </div>

          <div>
            <span className={LABEL}>Preview</span>
            <div className="mt-1">
              <ThemePreview
                draft={colors}
                brandName={brandName}
                logoUrl={branding?.logo_url ?? null}
              />
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <span className={LABEL}>Logo</span>
                <label className={`${SECONDARY} mt-1 cursor-pointer`}>
                  <Upload className="h-3.5 w-3.5" aria-hidden />
                  Upload
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) {
                        logoUpload.mutate(file);
                      }
                      event.target.value = "";
                    }}
                  />
                </label>
                {branding?.logo_file_id ? (
                  <button
                    type="button"
                    className={`${DANGER} mt-2`}
                    onClick={() => logoClear.mutate()}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    Remove
                  </button>
                ) : null}
              </div>
              <div>
                <span className={LABEL}>Favicon</span>
                <label className={`${SECONDARY} mt-1 cursor-pointer`}>
                  <Upload className="h-3.5 w-3.5" aria-hidden />
                  Upload
                  <input
                    type="file"
                    accept="image/png,image/x-icon,image/webp"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) {
                        faviconUpload.mutate(file);
                      }
                      event.target.value = "";
                    }}
                  />
                </label>
                {branding?.favicon_file_id ? (
                  <button
                    type="button"
                    className={`${DANGER} mt-2`}
                    onClick={() => faviconClear.mutate()}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    Remove
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ---- Sender domain ---- */}
      <section className={CARD}>
        <h2 className="text-base font-semibold">Notification sender domain</h2>
        <p className={`${HINT} mt-1`}>
          Send notifications from your own domain. Until SPF and DKIM verify,
          mail goes out from the FlowPilot platform address.
        </p>

        {branding?.sender.degradation_reason ? (
          /*
           * The server's sentence, rendered verbatim. ARCH-25 invariant 5
           * requires a lapsed sender domain to degrade visibly, and putting
           * this copy in a local switch over sender_domain_status is how it
           * eventually goes quiet again.
           */
          <p className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            {branding.sender.degradation_reason}
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap items-end gap-2">
          <div className="min-w-64 flex-1">
            <label className={LABEL} htmlFor="sender-domain">
              Sender domain
            </label>
            <input
              id="sender-domain"
              className={INPUT}
              placeholder="mail.acme.com"
              value={senderDraft ?? branding?.sender.sender_domain ?? ""}
              onChange={(event) => setSenderDraft(event.target.value)}
            />
          </div>
          <button
            type="button"
            className={PRIMARY}
            disabled={saveSender.isPending}
            onClick={() =>
              saveSender.mutate(
                (senderDraft ?? "").trim() === ""
                  ? null
                  : (senderDraft ?? "").trim(),
              )
            }
          >
            Save
          </button>
          <button
            type="button"
            className={SECONDARY}
            disabled={
              checkSender.isPending || !branding?.sender.sender_domain
            }
            onClick={() => checkSender.mutate()}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Check records
          </button>
          <span className={HINT}>
            {SENDER_STATUS_LABELS[
              branding?.sender.sender_domain_status ?? "UNSET"
            ]}
            {branding?.sender.sender_domain_checked_at
              ? ` · checked ${formatDateTime(branding.sender.sender_domain_checked_at)}`
              : ""}
          </span>
        </div>

        {branding && branding.sender.required_records.length > 0 ? (
          <ul className="mt-4 space-y-2">
            {branding.sender.required_records.map((record) => (
              <li
                key={record.purpose}
                className="rounded-md border border-border p-3 text-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{record.purpose}</span>
                  <span className={HINT}>
                    {record.present ? "Found" : "Not found"}
                  </span>
                </div>
                <p className="mt-1 font-mono text-xs break-all">
                  {record.record_name}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <p className="font-mono text-xs break-all">
                    {record.record_value}
                  </p>
                  <CopyButton
                    value={record.record_value}
                    label={`${record.purpose} value`}
                  />
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
};

export default OrganizationBranding;
