import { CAPABILITY, type CapabilityKey } from "@/constants/capabilities";

/**
 * HM-S1:plan-features — how each premium grant is named on a plan card.
 *
 * The WHAT comes from the server: a plan card lists the `capability.*` and
 * `addon.*` rows its published tier version carries, so the card cannot claim
 * a feature the request path would refuse. This file only owns the WORDS, and
 * `Record<PlanFeatureKey, string>` makes a new capability without a label a
 * compile error rather than a schema key on a pricing page.
 */
export type AddonFeatureKey = "addon.custom_domain" | "addon.warehouse_sync";
export type PlanFeatureKey = CapabilityKey | AddonFeatureKey;

export const PLAN_FEATURE_LABELS: Record<PlanFeatureKey, string> = {
  [CAPABILITY.developerApi]: "Developer API keys",
  [CAPABILITY.outgoingWebhooks]: "Outgoing webhooks",
  [CAPABILITY.customBranding]: "Custom branding",
  "addon.custom_domain": "Vanity domains",
  [CAPABILITY.reconciliation]: "Autonomous three-way reconciliation",
  [CAPABILITY.anomalyRadar]: "Forensic anomaly & duplicate radar",
  [CAPABILITY.customEmail]: "Custom email overrides",
  // ARCH41-S3:plan-feature-label
  [CAPABILITY.extractionMemory]: "Extraction memory (learns from reviewed corrections)",
  // ARCH42-S2:plan-feature
  [CAPABILITY.entityGraph]: "Entity graph (one record per vendor, customer and person across documents)",
  // ARCH43-S2:plan-feature
  [CAPABILITY.caseIntelligence]: "Case intelligence & packet dicer (split scanned bundles into documents)",
  // ARCH44-S2:plan-feature
  [CAPABILITY.tableIntelligence]: "Table intelligence (multi-page, rotated and ruled tables, validated, CSV/XLSX export)",
  "addon.warehouse_sync": "Analytics warehouse egress",
  [CAPABILITY.calibratedAutonomy]: "Calibrated autonomy & conformal risk",
  [CAPABILITY.redaction]: "Zero-leakage geometric PII redaction",
  [CAPABILITY.semanticAssertions]: "Semantic clause assertions",
  [CAPABILITY.enterpriseIdentity]: "Enterprise SAML SSO & SCIM directory sync",
  [CAPABILITY.prioritySlo]: "Priority 99.9% SLO",
};

/** Display order: the order in which the tiers add them. */
export const PLAN_FEATURE_ORDER: readonly PlanFeatureKey[] = [
  CAPABILITY.developerApi,
  CAPABILITY.outgoingWebhooks,
  CAPABILITY.customBranding,
  "addon.custom_domain",
  CAPABILITY.reconciliation,
  CAPABILITY.anomalyRadar,
  CAPABILITY.customEmail,
  "addon.warehouse_sync",
  CAPABILITY.calibratedAutonomy,
  CAPABILITY.redaction,
  CAPABILITY.semanticAssertions,
  CAPABILITY.enterpriseIdentity,
  CAPABILITY.prioritySlo,
];

export const isPlanFeatureKey = (key: string): key is PlanFeatureKey =>
  Object.prototype.hasOwnProperty.call(PLAN_FEATURE_LABELS, key);

/** What every plan does, Free included. Product facts, not entitlements. */
export const CORE_FEATURES: readonly string[] = [
  "Standard document extraction & OCR",
  "Workspace AI chat",
];

export const TIER_RANK: Readonly<Record<string, number>> = {
  free: 0,
  developer: 1,
  business: 2,
  enterprise: 3,
};
