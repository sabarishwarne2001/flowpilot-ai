/**
 * ARCH36-S1:capability-constants — the frontend's one copy of the backend's
 * `CAPABILITY_KEYS` (app/core/entitlements.py).
 *
 * Before ARCH-36 each capability-gated page declared its own string literal.
 * Five copies of a string the backend owns is five places a rename can miss,
 * and a missed rename is not a crash: `useCapabilityAccess` reports the
 * capability absent and the page shows its lock card to a tenant who paid.
 *
 * `verify_arch36.py` fails when this set and the backend set differ, and when
 * any `"capability.*"` literal anywhere under `src/` is not one of these.
 */
/**
 * HM-S1:capability-constants — six keys added with the four-tier packaging:
 * the developer API, outgoing webhooks, custom branding, custom email,
 * enterprise identity (SAML/SCIM) and the priority SLO.
 */
export const CAPABILITY = {
  reconciliation: "capability.reconciliation",
  redaction: "capability.redaction",
  semanticAssertions: "capability.semantic_assertions",
  anomalyRadar: "capability.anomaly_radar",
  calibratedAutonomy: "capability.calibrated_autonomy",
  developerApi: "capability.developer_api",
  outgoingWebhooks: "capability.outgoing_webhooks",
  customBranding: "capability.custom_branding",
  customEmail: "capability.custom_email",
  enterpriseIdentity: "capability.enterprise_identity",
  prioritySlo: "capability.priority_slo",
  // ARCH41-S3:capability-constant
  extractionMemory: "capability.extraction_memory",
} as const;

export type CapabilityKey = (typeof CAPABILITY)[keyof typeof CAPABILITY];

export const CAPABILITY_KEYS: readonly CapabilityKey[] = Object.values(CAPABILITY);
