/**
 * ARCH-25 — white-label, custom domains and tenant branding.
 *
 * These mirror the Pydantic models in `app/schemas/custom_domain.py` and
 * `app/schemas/tenant_branding.py`. Where the backend computes something, the
 * type carries the computed value rather than the inputs — see
 * `may_request_certificate` and `degradation_reason` below.
 */

export type CustomDomainStatus = "PENDING" | "VERIFIED" | "FAILED" | "REVOKED";

export type CertificateStatus =
  | "NONE"
  | "PENDING"
  | "ISSUED"
  | "FAILED"
  | "EXPIRED";

export type ColorScheme = "LIGHT" | "DARK" | "SYSTEM";

export type SenderDomainStatus = "UNSET" | "PENDING" | "VERIFIED" | "LAPSED";

export interface DnsChallengeInstructions {
  readonly record_name: string;
  readonly record_type: "TXT";
  readonly record_value: string;
  readonly ttl_hint_seconds: number;
  readonly expires_at: string;
}

export interface CustomDomainResponse {
  readonly id: string;
  readonly organization_id: string;
  readonly hostname: string;
  readonly status: CustomDomainStatus;
  readonly is_primary: boolean;
  readonly challenge_token: string;
  readonly challenge_expires_at: string;
  readonly verified_at: string | null;
  readonly last_checked_at: string | null;
  readonly last_failure_reason: string | null;
  readonly consecutive_failures: number;
  readonly certificate_status: CertificateStatus;
  readonly certificate_issued_at: string | null;
  readonly certificate_expires_at: string | null;
  readonly certificate_last_error: string | null;
  readonly revoked_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CustomDomainDetail extends CustomDomainResponse {
  readonly challenge: DnsChallengeInstructions;
  /**
   * Sent by the server, never derived here.
   *
   * ARCH-24 established that when the backend owns a threshold the console
   * renders the boolean it was given. Deriving this from
   * `status === "VERIFIED"` would enable a button the server then refuses,
   * and the refusal would read as a bug rather than as policy.
   * verify_arch25.py G17 fails if the page starts deriving it.
   */
  readonly may_request_certificate: boolean;
}

export interface DomainVerificationResult {
  readonly hostname: string;
  readonly verified: boolean;
  readonly status: CustomDomainStatus;
  /**
   * True when no DNS resolver answered.
   *
   * Distinct from `verified: false`, and the distinction is the point: a
   * resolver outage is ours, a missing record is theirs. The console must not
   * tell a customer to check their DNS during our own incident.
   */
  readonly resolver_failed: boolean;
  readonly checked_at: string;
  readonly detail: string;
  readonly records_seen: number;
}

export interface CertificateStatusResponse {
  readonly hostname: string;
  readonly certificate_status: CertificateStatus;
  readonly certificate_issued_at: string | null;
  readonly certificate_expires_at: string | null;
  readonly certificate_serial: string | null;
  readonly certificate_last_error: string | null;
  readonly days_until_expiry: number | null;
}

export interface SenderDnsRecord {
  readonly purpose: "SPF" | "DKIM" | "DMARC";
  readonly record_name: string;
  readonly record_type: "TXT";
  readonly record_value: string;
  readonly present: boolean;
}

export interface SenderDomainStatusResponse {
  readonly sender_domain: string | null;
  readonly sender_domain_status: SenderDomainStatus;
  readonly sender_domain_checked_at: string | null;
  readonly sender_domain_last_error: string | null;
  readonly may_send_as_tenant: boolean;
  /**
   * A server-written sentence naming the domain, or null when mail is not
   * degraded. ARCH-25 invariant 5 requires a lapsed sender domain to degrade
   * VISIBLY; rendering this string is how that happens. A `switch` over
   * `sender_domain_status` in the page would eventually grow a `default`
   * branch and the lapse would go quiet again.
   */
  readonly degradation_reason: string | null;
  readonly required_records: readonly SenderDnsRecord[];
}

export interface TenantBrandingResponse {
  readonly id: string;
  readonly organization_id: string;
  readonly brand_name: string | null;
  readonly logo_file_id: string | null;
  readonly favicon_file_id: string | null;
  readonly logo_url: string | null;
  readonly favicon_url: string | null;
  readonly primary_color: string | null;
  readonly accent_color: string | null;
  readonly background_color: string | null;
  readonly foreground_color: string | null;
  readonly color_scheme: ColorScheme;
  readonly support_email: string | null;
  readonly is_enabled: boolean;
  readonly sender: SenderDomainStatusResponse;
  readonly updated_at: string;
}

/**
 * Partial update. Only the keys present are applied.
 *
 * There is deliberately no `logo_file_id` or `favicon_file_id` here, and the
 * backend schema sets `extra="forbid"`, so sending one is a 422. Assets move
 * only through the upload endpoints, which are the only paths that can prove
 * the stored object belongs to this tenant before writing the reference.
 */
export interface TenantBrandingUpdate {
  brand_name?: string | null;
  primary_color?: string | null;
  accent_color?: string | null;
  background_color?: string | null;
  foreground_color?: string | null;
  color_scheme?: ColorScheme;
  support_email?: string | null;
  is_enabled?: boolean;
}

export interface SenderDomainUpdate {
  sender_domain: string | null;
}

export interface CustomDomainCreate {
  hostname: string;
}

/** The unauthenticated, host-resolved theme payload. Carries no identifiers. */
export interface BrandingManifest {
  readonly brand_name: string | null;
  readonly primary_color: string | null;
  readonly accent_color: string | null;
  readonly background_color: string | null;
  readonly foreground_color: string | null;
  readonly color_scheme: ColorScheme;
  readonly logo_url: string | null;
  readonly favicon_url: string | null;
  readonly support_email: string | null;
  readonly has_custom_branding: boolean;
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

export const DOMAIN_STATUS_LABELS: Record<CustomDomainStatus, string> = {
  PENDING: "Awaiting DNS",
  VERIFIED: "Verified",
  FAILED: "Verification failed",
  REVOKED: "Revoked",
};

export const DOMAIN_STATUS_CLASSES: Record<CustomDomainStatus, string> = {
  PENDING: "bg-amber-100 text-amber-800",
  VERIFIED: "bg-emerald-100 text-emerald-800",
  FAILED: "bg-red-100 text-red-800",
  REVOKED: "bg-muted text-muted-foreground",
};

export const CERTIFICATE_STATUS_LABELS: Record<CertificateStatus, string> = {
  NONE: "No certificate",
  PENDING: "Issuing",
  ISSUED: "Active",
  FAILED: "Issuance failed",
  EXPIRED: "Expired",
};

export const SENDER_STATUS_LABELS: Record<SenderDomainStatus, string> = {
  UNSET: "Not configured",
  PENDING: "Awaiting DNS",
  VERIFIED: "Verified",
  // Distinct copy from UNSET on purpose. "Not configured" and "stopped
  // working" are different problems and a tenant needs to be able to tell
  // which one they have.
  LAPSED: "Stopped verifying",
};

/**
 * The client-side mirror of `^#[0-9a-f]{6}$`.
 *
 * Present so a typo is caught before a round trip, NOT as the enforcement
 * point. The enforcement is a CHECK constraint plus a Pydantic validator; if
 * this and those ever disagree, those win. Uppercase is normalised here the
 * same way the backend normalises it.
 */
const HEX_COLOR_RE = /^#[0-9a-f]{6}$/;

export const normaliseHexColor = (value: string): string | null => {
  const candidate = value.trim().toLowerCase();
  if (!candidate) {
    return null;
  }
  return HEX_COLOR_RE.test(candidate) ? candidate : null;
};

export const isValidHexColor = (value: string): boolean =>
  normaliseHexColor(value) !== null;

export const formatDateTime = (value: string | null): string =>
  value ? new Date(value).toLocaleString() : "—";
