import React from "react";
import { Lock } from "lucide-react";

import { HINT, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";

/**
 * ARCH-33 — what a workspace sees in place of clause assertions it does not
 * have.
 *
 * Mirrors `components/procurement/CapabilityLockCard` and
 * `components/redaction/RedactionLockCard` exactly, including the absence of a
 * button: a capability is bundled into a tier, so the only route to it is a
 * plan change, and a purchase button would send the reader to a flow with
 * nothing to sell them. That is the same reasoning `capability_gate.py` gives
 * for returning `remedy: PLAN_UPGRADE` with no price.
 */
export const AssertionLockCard: React.FC<{ readonly canChangePlan: boolean }> = ({
  canChangePlan,
}) => (
  <section className={`${SURFACE} space-y-3 p-6`} aria-labelledby="assertion-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
      <h2 id="assertion-lock" className={SECTION_TITLE}>
        Clause assertions
      </h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Write a requirement as a workflow step — "payment terms do not exceed Net
      30" — and let documents that clearly meet it continue on their own.
      Anything uncertain or failing lands in the review queue with the
      paragraph attached.
    </p>
    <p className={HINT}>
      {canChangePlan
        ? "It's included on higher plans. Change your plan to turn it on."
        : "It's included on higher plans. Ask an organization owner to change your plan."}
    </p>
  </section>
);

export default AssertionLockCard;
