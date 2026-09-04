/**
 * ARCH-27 — partner marketplace, reseller tenancy and revenue share.
 *
 * WHY THE MONEY FIELDS ARE `number | null` AND NOT `number`
 * =========================================================
 *
 * `supplierCostMicros` and `marginMicros` are nullable on the database column,
 * nullable on the Pydantic response model, and nullable here. A TypeScript
 * type that declares them `number` compiles fine, renders `0`, and tells a
 * reseller they earned 100% margin on revenue whose supplier cost nobody
 * knows.
 *
 * `verify_arch27.py` G12 asserts the nullability survives on the backend. This
 * file is the other half of that guarantee: the compiler forces every call
 * site to decide what "unknown" looks like on screen.
 *
 * WHY `digestMatches` IS NOT RECOMPUTED IN THE BROWSER
 * ===================================================
 *
 * It travels from the backend, which recomputes the SHA-256 over the canonical
 * payload at request time. The same rule ARCH-24 applied to `isTrustworthy`: a
 * hash evaluated independently on two sides eventually disagrees, and the side
 * the user is looking at is the one that is wrong.
 */

export type PartnerStatus = "ACTIVE" | "SUSPENDED" | "TERMINATED";
export type PartnerMemberRole = "OWNER" | "ADMIN" | "ANALYST";
export type PartnerMemberStatus = "ACTIVE" | "SUSPENDED";
export type AssignmentStatus = "ACTIVE" | "ENDED";
export type SigningAlgorithm = "ED25519" | "RSA_PSS_SHA256";
export type SigningKeyStatus = "ACTIVE" | "REVOKED";
export type AgreementBasis = "GROSS_MARGIN" | "NET_REVENUE";
export type UnknownCostBasisPolicy = "EXCLUDE" | "FAIL";
export type PayoutPeriodStatus = "DRAFT" | "SEALED" | "PAID" | "VOID";
export type MarketplaceVisibility = "PUBLIC" | "PARTNER_ONLY";
export type MarketplaceItemStatus =
  | "DRAFT"
  | "PUBLISHED"
  | "DEPRECATED"
  | "WITHDRAWN";
export type InstallationStatus = "INSTALLED" | "DISABLED" | "REMOVED";

/**
 * The three-way split IS invariant 4.
 *
 * A ZERO_BYOK line can never be folded into a SUPPLIER_COST line: the two have
 * mutually exclusive CHECK constraints and the ledger's unique key keeps them
 * apart. Modelling this as a union rather than a boolean flag means a `switch`
 * that forgets a case fails to compile.
 */
export type RevShareBasisClass =
  | "SUPPLIER_COST"
  | "ZERO_BYOK"
  | "UNKNOWN_COST_BASIS";

export interface Partner {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
  readonly status: PartnerStatus;
  readonly owner_organization_id: string;
  readonly billing_email: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface PartnerMember {
  readonly id: string;
  readonly partner_id: string;
  readonly user_id: string;
  readonly role: PartnerMemberRole;
  readonly status: PartnerMemberStatus;
  readonly created_at: string;
}

export interface BookOfBusinessEntry {
  readonly id: string;
  readonly organization_id: string;
  readonly organization_name: string;
  readonly organization_slug: string;
  readonly status: AssignmentStatus;
  readonly effective_from: string;
  readonly effective_to: string | null;
}

export interface SigningKey {
  readonly id: string;
  readonly partner_id: string;
  readonly key_id: string;
  readonly algorithm: SigningAlgorithm;
  readonly fingerprint: string;
  readonly status: SigningKeyStatus;
  readonly revoked_at: string | null;
  readonly revocation_reason: string | null;
  readonly created_at: string;
}

export interface RevShareAgreement {
  readonly id: string;
  readonly partner_id: string;
  readonly name: string;
  readonly basis: AgreementBasis;
  readonly share_bps: number;
  readonly zero_byok_share_bps: number | null;
  readonly currency: string;
  readonly minimum_payout_micros: number;
  readonly unknown_cost_basis_policy: UnknownCostBasisPolicy;
  readonly effective_from: string;
  readonly effective_to: string | null;
  readonly status: "ACTIVE" | "ENDED";
  readonly created_at: string;
}

export interface RevShareLedgerLine {
  readonly id: string;
  readonly organization_id: string;
  readonly organization_name: string | null;
  readonly basis_class: RevShareBasisClass;
  readonly revenue_micros: number;
  /** NULL for UNKNOWN_COST_BASIS. Never render as 0. */
  readonly supplier_cost_micros: number | null;
  /** NULL for UNKNOWN_COST_BASIS. Never render as 0. */
  readonly margin_micros: number | null;
  readonly share_bps: number;
  readonly payout_micros: number;
  readonly event_count: number;
  readonly unknown_cost_basis_event_count: number;
  /** The sealed usage_rollups this line was computed from. Invariant 3. */
  readonly source_rollup_ids: readonly string[];
  readonly cost_basis_source_mix: Readonly<Record<string, number>> | null;
}

export interface PayoutPeriod {
  readonly id: string;
  readonly partner_id: string;
  readonly agreement_id: string;
  readonly period_start: string;
  /** Last day covered, INCLUSIVE. */
  readonly period_end: string;
  readonly status: PayoutPeriodStatus;
  readonly currency: string;

  readonly gross_revenue_micros: number;
  readonly supplier_cost_micros: number | null;
  readonly margin_micros: number | null;
  readonly payout_micros: number;
  readonly carried_forward_micros: number;

  /** Invariant 4. Required, not optional: a statement that can omit the
   *  ZERO_BYOK split is a statement that eventually does. */
  readonly zero_byok_revenue_micros: number;
  readonly zero_byok_margin_micros: number;
  readonly zero_byok_payout_micros: number;

  readonly excluded_revenue_micros: number;
  readonly excluded_unknown_cost_basis_event_count: number;
  readonly organization_count: number;
  readonly source_rollup_count: number;

  /** Empty string while DRAFT. */
  readonly content_digest: string;
  readonly sealed_at: string | null;
  readonly paid_at: string | null;
  readonly payment_reference: string | null;
  readonly created_at: string;
}

export interface PayoutStatement {
  readonly period: PayoutPeriod;
  readonly lines: readonly RevShareLedgerLine[];
  /** Computed server-side. The browser never recomputes this. */
  readonly digest_matches: boolean;
  readonly recomputed_digest: string;
}

export interface PartnerEconomics {
  readonly partner_id: string;
  readonly currency: string;
  readonly organization_count: number;
  readonly sealed_period_count: number;
  readonly lifetime_revenue_micros: number;
  /** NULL when no sealed period carried a margin. Not a margin of nil. */
  readonly lifetime_margin_micros: number | null;
  readonly lifetime_payout_micros: number;
  readonly lifetime_zero_byok_revenue_micros: number;
  readonly lifetime_excluded_revenue_micros: number;
  readonly zero_byok_revenue_share_bps: number;
}

export interface ManifestNode {
  readonly node_key: string;
  readonly node_type: "trigger" | "condition" | "action" | "branch" | "join";
  readonly config: Readonly<Record<string, unknown>>;
}

export interface ManifestEdge {
  readonly from_node_key: string;
  readonly to_node_key: string;
  readonly branch: "default" | "true" | "false";
}

export interface ManifestSignature {
  readonly id: string;
  readonly algorithm: SigningAlgorithm;
  readonly signed_digest: string;
  readonly verified_at: string;
  readonly signing_key_fingerprint: string | null;
  readonly signing_key_status: SigningKeyStatus | null;
}

export interface Manifest {
  readonly id: string;
  readonly item_id: string;
  readonly version: string;
  readonly status: "PUBLISHED" | "WITHDRAWN";
  readonly content_digest: string;
  readonly node_count: number;
  readonly edge_count: number;
  readonly published_at: string | null;
  readonly signatures: readonly ManifestSignature[];
}

export interface ManifestDetail {
  readonly manifest: Manifest;
  readonly nodes: readonly ManifestNode[];
  readonly edges: readonly ManifestEdge[];
  /** Verified server-side at request time, against the live key status. */
  readonly signature_verified: boolean;
  readonly verified_key_fingerprint: string | null;
}

export interface MarketplaceItem {
  readonly id: string;
  readonly partner_id: string;
  readonly partner_name: string | null;
  readonly slug: string;
  readonly name: string;
  readonly summary: string | null;
  readonly category: string;
  readonly status: MarketplaceItemStatus;
  readonly visibility: MarketplaceVisibility;
  readonly latest_version: string | null;
  readonly latest_manifest_id: string | null;
  readonly installed: boolean;
  readonly created_at: string;
}

export interface Installation {
  readonly id: string;
  readonly organization_id: string;
  readonly item_id: string;
  readonly item_name: string | null;
  readonly manifest_id: string;
  readonly manifest_version: string | null;
  readonly verified_signature_id: string;
  readonly automation_rule_id: string | null;
  readonly status: InstallationStatus;
  readonly installed_at: string;
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

export interface AssignOrganizationRequest {
  readonly organization_id: string;
  readonly effective_from?: string;
}

export interface ComputePayoutRequest {
  readonly period_start: string;
  readonly period_end: string;
}

export interface RegisterSigningKeyRequest {
  readonly key_id: string;
  readonly algorithm: SigningAlgorithm;
  /** PUBLIC half only. There is no field here for a private key. */
  readonly public_key_pem: string;
}

export interface InstallManifestRequest {
  readonly manifest_id: string;
  readonly workspace_id: string;
  readonly rule_name?: string;
  /** Defaults to false server-side. Third-party code does not start firing
   *  on live documents the instant it is installed. */
  readonly enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

/** Micros to a currency string. `null` renders as an em dash, never as zero. */
export const formatMicros = (
  micros: number | null,
  currency: string = "USD",
): string => {
  if (micros === null || micros === undefined) {
    return "—";
  }
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(micros / 1_000_000);
};

/** Basis points to a percentage string. */
export const formatBps = (bps: number): string =>
  `${(bps / 100).toFixed(bps % 100 === 0 ? 0 : 2)}%`;

/**
 * What a basis class means, in the words a reseller needs.
 *
 * Held here rather than inline in JSX so the three explanations stay in one
 * place and stay consistent between the ledger table and the statement header.
 */
export const BASIS_CLASS_LABEL: Readonly<
  Record<RevShareBasisClass, { label: string; tone: string; hint: string }>
> = {
  SUPPLIER_COST: {
    label: "Supplier cost",
    tone: "slate",
    hint: "Margin computed against a known supplier cost.",
  },
  ZERO_BYOK: {
    label: "BYOK — 100% margin",
    tone: "emerald",
    hint: "The tenant pays the model provider directly, so this revenue carries no supplier cost to us.",
  },
  UNKNOWN_COST_BASIS: {
    label: "Excluded — cost unknown",
    tone: "amber",
    hint: "Supplier cost for this revenue is unknown or partial, so margin is only an upper bound. Nothing is paid on an upper bound.",
  },
};
