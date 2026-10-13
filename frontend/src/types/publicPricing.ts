/**
 * Campaign session 4 — the public price list (`GET /api/v1/pricing`).
 *
 * Mirrors backend/app/schemas/public_pricing.py. Built by the server from the
 * published plans (the same helpers as the in-app plan cards), so the public
 * /pricing page never types a price or an allowance of its own.
 */

/** One way to buy a plan. `unit_amount` is per seat per `interval`, in minor units (cents). */
export interface PublicPlanPrice {
  readonly interval: string;
  readonly currency: string;
  readonly unit_amount: number;
}

/** A count a customer plans by, as enforced. `limit_quantity` null: no ceiling of its own. */
export interface PublicPlanAllowance {
  readonly key: string;
  readonly limit_quantity: number | null;
  readonly period: string;
  /** REFUSE, ALLOW_AND_BILL or ALLOW_AND_WARN past the ceiling; null where there is none. */
  readonly overage_policy: string | null;
}

export interface PublicPlan {
  readonly key: string;
  readonly display_name: string;
  readonly is_priced: boolean;
  /** Paid plans: allowances are per seat, pooled across the organization. */
  readonly per_seat: boolean;
  readonly prices: readonly PublicPlanPrice[];
  readonly allowances: readonly PublicPlanAllowance[];
  /** `capability.*` and `addon.*` keys the plan's published version grants. */
  readonly features: readonly string[];
}

export interface PublicTrial {
  readonly plan_key: string;
  readonly days: number;
}

export interface PublicPricingResponse {
  readonly as_of: string;
  readonly trial: PublicTrial;
  readonly plans: readonly PublicPlan[];
}
