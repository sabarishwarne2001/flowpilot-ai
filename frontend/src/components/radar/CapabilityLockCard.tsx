import React from "react";
import { Lock } from "lucide-react";

import { HINT, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";

/**
 * ARCH-34 — what a workspace sees in place of the audit radar it does not
 * have. Mirrors `components/procurement/CapabilityLockCard` in shape, and
 * diverges only in the words.
 *
 * There is no button, for the reason that card records: an add-on can be
 * bought on the plan the customer already has, so its card opens checkout; a
 * capability is bundled into a tier and the only route to it is a plan
 * change. `capability_gate.py` returns `remedy: PLAN_UPGRADE` with no price
 * for exactly this reason, and a purchase button here would send the reader
 * into a flow with nothing to sell them.
 */
export const CapabilityLockCard: React.FC<{ readonly canChangePlan: boolean }> = ({
  canChangePlan,
}) => (
  <section className={`${SURFACE} space-y-3 p-6`} aria-labelledby="radar-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
      <h2 id="radar-lock" className={SECTION_TITLE}>
        Forensic audit radar
      </h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Catch the same invoice arriving twice under a different name, unit prices
      creeping across months, and contract versions that quietly disagree on
      terms &mdash; each raised with the two documents side by side so you can
      check it yourself.
    </p>
    <p className={HINT}>
      {canChangePlan
        ? "It's included on the Business and Enterprise plans. Change your plan to turn it on."
        : "It's included on the Business and Enterprise plans. Ask an organization owner to change your plan."}
    </p>
  </section>
);

export default CapabilityLockCard;
