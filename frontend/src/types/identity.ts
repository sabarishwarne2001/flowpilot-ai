/**
 * ARCH-16 enterprise identity DTOs.
 * Mirrors `app/schemas/identity.py`.
 */

export type DomainStatus =
  | "PENDING"
  | "VERIFIED"
  | "LAPSED"
  | "REVOKED"
  | string;

export interface DomainRead {
  readonly id: string;
  readonly domain: string;
  readonly status: DomainStatus;
  readonly is_sso_binding: boolean;
  readonly expected_txt_record: string;
  readonly challenge_expires_at: string | null;
  readonly last_checked_at: string | null;
  readonly last_seen_at: string | null;
  readonly grace_expires_at: string | null;
  readonly provisioning_allowed: boolean;
}

export interface DomainClaimResponse {
  readonly id: string;
  readonly domain: string;
  readonly status: DomainStatus;
  readonly expected_txt_record: string;
  readonly instructions: string;
}

export interface DomainClaimRequest {
  readonly domain: string;
}

export type IdpProtocol = "SAML2" | "OIDC";

export type JitProvisioningMode = "OPEN" | "CAPPED" | "INVITE_ONLY";

export type OrganizationRoleName = "ADMIN" | "BILLING" | "MEMBER";

export interface IdpConfigCreate {
  readonly verified_domain_id: string;
  readonly protocol: IdpProtocol;
  readonly display_name: string;

  readonly idp_entity_id?: string | null;
  readonly idp_sso_url?: string | null;
  readonly idp_slo_url?: string | null;
  readonly metadata_url?: string | null;
  readonly allow_unsolicited?: boolean;
  readonly name_id_format?: string | null;

  readonly oidc_issuer?: string | null;
  readonly oidc_client_id?: string | null;
  readonly oidc_client_secret?: string | null;
  readonly oidc_discovery_url?: string | null;

  readonly jit_provisioning_mode?: JitProvisioningMode;
  readonly jit_default_org_role?: OrganizationRoleName;
  readonly jit_seat_cap?: number | null;
  readonly force_reauth_max_age_s?: number | null;
}

export interface IdpConfigRead {
  readonly id: string;
  readonly protocol: string;
  readonly display_name: string;
  readonly is_active: boolean;
  readonly idp_entity_id: string | null;
  readonly idp_sso_url: string | null;
  readonly oidc_issuer: string | null;
  readonly jit_provisioning_mode: string;
  readonly jit_default_org_role: string;
  readonly jit_seat_cap: number | null;
  readonly current_billable_seats: number;
  readonly effective_seat_cap: number | null;
}

export type CertificateSide = "IDP" | "SP";

export interface CertificateCreate {
  readonly certificate_pem: string;
  readonly side?: CertificateSide;
  readonly is_primary?: boolean;
}

export interface CertificateRead {
  readonly id: string;
  readonly side: string;
  readonly not_before: string | null;
  readonly not_after: string | null;
  readonly is_primary: boolean;
  readonly retired_at: string | null;
}

export type RoleMatchKind = "EQUALS" | "CONTAINS" | "PREFIX";

export interface RoleMappingCreate {
  readonly priority?: number;
  readonly attribute_name: string;
  readonly match_kind?: RoleMatchKind;
  readonly match_value: string;
  readonly organization_role: OrganizationRoleName;
}

export interface DryRunRequest {
  readonly attributes: Record<string, string | string[]>;
}

export interface DryRunResult {
  readonly resolved_role: string;
  readonly would_consume_seat: boolean;
  readonly current_seats: number;
  readonly seat_cap: number | null;
}

export interface ScimKeyCreate {
  readonly idp_config_id: string;
  readonly display_name?: string;
}

export interface ScimKeyRead {
  readonly id: string;
  readonly display_name: string;
  readonly key_prefix: string;
  readonly scopes: readonly string[];
  readonly last_used_at: string | null;
  readonly previous_secret_expires_at: string | null;
  readonly previous_last_used_at: string | null;
  readonly expires_at: string | null;
  readonly revoked_at: string | null;
}

export interface ScimKeyIssued {
  readonly id: string;
  readonly token: string;
  readonly note: string;
}

export type IpPinningMode = "OFF" | "PREFIX" | "STRICT";

export interface SecurityPolicyRead {
  readonly require_sso: boolean;
  readonly sso_bypass_for_owners: boolean;
  readonly ip_pinning: string;
  readonly ip_allowlist: readonly string[];
  readonly max_session_age_s: number | null;
  readonly idp_session_sync: boolean;
}

export interface SecurityPolicyUpdate {
  readonly require_sso?: boolean;
  readonly sso_bypass_for_owners?: boolean;
  readonly ip_pinning?: IpPinningMode;
  readonly ip_allowlist?: readonly string[];
  readonly max_session_age_s?: number;
  readonly idp_session_sync?: boolean;
}

export interface DirectoryIdentityRead {
  readonly id: string;
  readonly user_name: string;
  readonly external_id: string;
  readonly active: boolean;
  readonly provisioned_via: string;
  readonly last_login_at: string | null;
  readonly last_synced_at: string | null;
  readonly deprovisioned_at: string | null;
  readonly deprovision_reason: string | null;
}

export interface SsoDiscoveryResult {
  readonly sso_enabled: boolean;
  readonly protocol: string | null;
  readonly display_name: string | null;
  readonly start_url: string | null;
}
