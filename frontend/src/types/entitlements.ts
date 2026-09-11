/**
 * ARCH-30 Tranche 2 (D-8) — add-on entitlements.
 *
 * Mirrors `app/schemas/entitlements.py`. Every boolean the console acts on —
 * `can_create`, `can_maintain`, `purchasable` — is computed by the backend and
 * sent. The console never re-derives them from `state`, so a button can never
 * be enabled over an endpoint that answers 402.
 */

export type AddonKey = "addon.custom_domain" | "addon.warehouse_sync";

export type AddonState = "ACTIVE" | "GRACE" | "LAPSED" | "NOT_GRANTED";

export interface AddonAccess {
  readonly addon_key: AddonKey;
  readonly display_name: string;
  readonly description: string;
  readonly state: AddonState;
  readonly source: "TIER" | "SUBSCRIPTION" | null;
  readonly grace_ends_at: string | null;
  readonly can_create: boolean;
  readonly can_maintain: boolean;
  readonly live_resource_count: number;
  readonly halt_effect: string;
  readonly monthly_price_micros: number;
  readonly currency: string;
  readonly purchasable: boolean;
  readonly included_in: readonly string[];
}

export interface OrganizationEntitlements {
  readonly organization_id: string;
  readonly as_of: string;
  readonly addons: readonly AddonAccess[];
}

/** The structured 402 body from `app/api/addon_gate.py`. */
export interface AddonRequiredDetail {
  readonly code: "ADDON_REQUIRED";
  readonly addon_key: AddonKey;
  readonly addon_name: string;
  readonly state: AddonState;
  readonly grace_ends_at: string | null;
  readonly message: string;
}
