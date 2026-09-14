import React from "react";
import { Lock } from "lucide-react";

import { HINT, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";

/**
 * ARCH-31 Step 4 — what a workspace sees in place of procurement matching it
 * does not have. Mirrors `AddOnLockCard` in shape and diverges in the remedy.
 *
 * There is no button. An add-on can be bought on the plan the customer
 * already has, so its card opens checkout; a capability is bundled into a
 * tier and the only route to it is a plan change. Rendering a purchase button
 * here would send the reader to a flow with nothing to sell them — the same
 * reasoning `capability_gate.py` gives for returning `remedy: PLAN_UPGRADE`
 * with no price.
 */
export const CapabilityLockCard: React.FC<{ readonly canChangePlan: boolean }> = ({
  canChangePlan,
}) => (
  <section className={`${SURFACE} space-y-3 p-6`} aria-labelledby="procurement-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
      <h2 id="procurement-lock" className={SECTION_TITLE}>
        Procurement matching
      </h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Match every supplier invoice against its purchase order and goods receipt
      automatically. Lines that differ in price or quantity, or that nobody
      ordered, are flagged for review before the invoice is paid.
    </p>
    <p className={HINT}>
      {canChangePlan
        ? "It's included on the Business and Enterprise plans. Change your plan to turn it on."
        : "It's included on the Business and Enterprise plans. Ask an organization owner to change your plan."}
    </p>
  </section>
);

export default CapabilityLockCard;
