import React from "react";
import { Lock } from "lucide-react";

import { HINT, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";

/**
 * ARCH-32 — what a workspace sees in place of redaction it does not have.
 *
 * Mirrors `components/procurement/CapabilityLockCard` exactly, including the
 * absence of a button: a capability is bundled into a tier, so the only route
 * to it is a plan change, and a purchase button would send the reader to a
 * flow with nothing to sell them. That is the same reasoning
 * `capability_gate.py` gives for returning `remedy: PLAN_UPGRADE` with no
 * price.
 */
export const RedactionLockCard: React.FC<{ readonly canChangePlan: boolean }> = ({
  canChangePlan,
}) => (
  <section className={`${SURFACE} space-y-3 p-6`} aria-labelledby="redaction-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
      <h2 id="redaction-lock" className={SECTION_TITLE}>
        Document redaction
      </h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Produce a file in which the redacted content does not exist. Every page is
      rebuilt from pixels, so hidden text, form fields, comments and metadata
      are removed rather than covered over.
    </p>
    <p className={HINT}>
      {canChangePlan
        ? "It's included on the Enterprise plan. Change your plan to turn it on."
        : "It's included on the Enterprise plan. Ask an organization owner to change your plan."}
    </p>
  </section>
);

export default RedactionLockCard;
