/**
 * ARCH50-S2:types-sovereign — the Sovereign Edition: an organization's egress lockdown and the operator's console.
 * Mirrors app/schemas/sovereign.py and the vocabulary in app/core/egress.py (verify_arch50 W3 proves the parity).
 */

export const TENANT_CHANNELS = [
  "WEBHOOK", "WAREHOUSE", "ERP_HTTP", "ERP_SFTP", "IDENTITY", "SMTP_TENANT", "LLM_PROVIDER",
] as const;
export type TenantChannel = (typeof TENANT_CHANNELS)[number];

export const CHANNELS = [
  ...TENANT_CHANNELS, "SMTP_PLATFORM", "LLM_LOCAL", "STORAGE", "BILLING", "INTERNAL", "DNS", "BACKUP",
] as const;
export type Channel = (typeof CHANNELS)[number];

export const CHANNEL_LABELS: Record<Channel, string> = {
  WEBHOOK: "Outgoing webhooks",
  WAREHOUSE: "Warehouse & BI egress",
  ERP_HTTP: "ERP posting (REST / OData)",
  ERP_SFTP: "ERP posting (SFTP)",
  IDENTITY: "SSO metadata (OIDC / SAML)",
  SMTP_TENANT: "Your SMTP server",
  LLM_PROVIDER: "AI model providers (platform and BYOK)",
  SMTP_PLATFORM: "Platform email relay",
  LLM_LOCAL: "Operator's local model",
  STORAGE: "Object storage",
  BILLING: "Payment gateway",
  INTERNAL: "Internal services (reranker)",
  DNS: "DNS resolvers (domain verification)",
  BACKUP: "Off-host backup mirror",
};

export const REASONS = [
  "DEPLOYMENT_DENY", "TENANT_LOCKDOWN", "OPERATOR_ONLY", "INVALID_DESTINATION", "POLICY_UNAVAILABLE",
] as const;
export type RefusalReason = (typeof REASONS)[number];

export const REASON_LABELS: Record<RefusalReason, string> = {
  DEPLOYMENT_DENY: "Denied by the deployment (air-gapped mode)",
  TENANT_LOCKDOWN: "Not on your allow list",
  OPERATOR_ONLY: "Reachable only at the operator's configured host",
  INVALID_DESTINATION: "Not a usable destination",
  POLICY_UNAVAILABLE: "Policy could not be read (refused, not allowed)",
};

export const EGRESS_MODES = ["open", "deny"] as const;
export type EgressMode = (typeof EGRESS_MODES)[number];

export interface EgressRule {
  readonly id: string;
  readonly channel: string | null;
  readonly host_pattern: string;
  readonly port: number | null;
  readonly note: string | null;
  readonly created_at: string;
  readonly created_by_user_id: string | null;
}

export interface EgressPolicy {
  readonly organization_id: string;
  readonly lockdown_enabled: boolean;
  readonly updated_at: string | null;
  readonly updated_by_user_id: string | null;
  readonly rules: readonly EgressRule[];
  readonly max_rules: number;
  readonly deployment_mode: string;
  readonly governed_channels: readonly string[];
}

export interface EgressRuleInput {
  readonly channel: TenantChannel | null;
  readonly host_pattern: string;
  readonly port: number | null;
  readonly note: string | null;
}

export interface EgressRefusal {
  readonly id: string;
  readonly organization_id: string | null;
  readonly channel: string;
  readonly host: string;
  readonly port: number;
  readonly reason: string;
  readonly mode: string;
  readonly bucket_start: string;
  readonly first_at: string;
  readonly last_at: string;
  readonly count: number;
}

export interface EgressRefusalList {
  readonly days: number;
  readonly refusals: readonly EgressRefusal[];
}

export interface EgressDecision {
  readonly allowed: boolean;
  readonly channel: string;
  readonly host: string;
  readonly port: number | null;
  readonly mode: string;
  readonly reason: string | null;
  readonly explanation: string;
  readonly organization_id: string | null;
  readonly matched: string | null;
}

export interface LicenceStatus {
  readonly status: string;
  readonly edition: string;
  readonly usable: boolean;
  readonly reason: string;
  readonly licence_id: string | null;
  readonly licensee: string | null;
  readonly key_id: string | null;
  readonly expires_at: string | null;
  readonly grace_until: string | null;
  readonly days_left: number | null;
  readonly max_organizations: number | null;
  readonly max_seats: number | null;
  readonly features: readonly string[];
  readonly source: string | null;
}

export interface ChannelSummary {
  readonly channel: Channel;
  readonly label: string;
  readonly tenant_governed: boolean;
  readonly operator_only: boolean;
  readonly declared: readonly string[];
  readonly opened_in: readonly string[];
}

export interface DrDrill {
  readonly id: string;
  readonly kind?: string;
  readonly outcome: string;
  readonly started_at: string;
  readonly finished_at: string;
  readonly target_time?: string | null;
  readonly recovered_to?: string | null;
  readonly rpo_seconds: string | number | null;
  readonly rto_seconds: string | number | null;
  readonly host: string;
}

export interface DrStatus {
  readonly targets: { readonly rpo_seconds: number; readonly rto_seconds: number };
  readonly latest: Record<string, DrDrill | null>;
  readonly recent: readonly DrDrill[];
  readonly heartbeat: { readonly latest: string | null; readonly lag_seconds: number | null };
  readonly meets_targets: boolean;
}

export interface SovereignStatus {
  readonly edition: string;
  readonly environment: string;
  readonly egress: { readonly mode: string; readonly channels: readonly ChannelSummary[] };
  readonly local_llm: Record<string, unknown>;
  readonly licence: LicenceStatus;
  readonly usage: { readonly organizations: number; readonly seats: number };
  readonly dr: DrStatus;
  readonly refusals_24h: number;
}

export interface LocalModelHealth {
  readonly configured: boolean;
  readonly mode: string;
  readonly model?: string;
  readonly host?: string;
  readonly reachable: boolean;
  readonly models: readonly string[];
  readonly model_served?: boolean;
  readonly latency_ms?: number;
  readonly error: string | null;
}

export interface ReleaseStatus {
  readonly status: string;
  readonly release_dir: string;
  readonly files: number;
  readonly modified: readonly string[];
  readonly missing: readonly string[];
  readonly key_id: string | null;
  readonly release: Record<string, unknown> | null;
}

export const LICENCE_STATUS_LABELS: Record<string, string> = {
  NOT_REQUIRED: "Not required (hosted edition)",
  VALID: "Valid",
  GRACE: "Expired — in grace period",
  EXPIRED: "Expired",
  NOT_YET_VALID: "Not valid yet",
  MISSING: "No licence installed",
  MALFORMED: "Unreadable",
  UNTRUSTED_KEY: "Signed by an untrusted key",
  BAD_SIGNATURE: "Signature does not match",
  DEVELOPMENT_KEY_IN_PRODUCTION: "Development key refused in production",
  WRONG_EDITION: "Issued for another edition",
  WRONG_DEPLOYMENT: "Bound to another deployment",
};
