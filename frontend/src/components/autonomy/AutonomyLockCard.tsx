import React from "react";
import { Lock } from "lucide-react";

import { HINT, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";

/**
 * ARCH-35 — what an organization sees in place of calibrated autonomy.
 * Mirrors `components/radar/CapabilityLockCard` and diverges only in words.
 *
 * No button, for the reason every capability lock card records: a capability
 * is bundled into a tier, the only route to it is a plan change, and
 * `capability_gate.py` answers `remedy: PLAN_UPGRADE` with no price.
 */
export const AutonomyLockCard: React.FC<{ readonly canChangePlan: boolean }> = ({
  canChangePlan,
}) => (
  <section className={`${SURFACE} space-y-3 p-6`} aria-labelledby="autonomy-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
      <h2 id="autonomy-lock" className={SECTION_TITLE}>
        Calibrated autonomy
      </h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Let documents through without review only as far as your own reviewed
      history supports, with a stated, measured limit on how often an automatic
      approval is wrong. Today your plan uses fixed confidence thresholds.
    </p>
    <p className={HINT}>
      {canChangePlan
        ? "It's included on the Enterprise plan. Change your plan to turn it on."
        : "It's included on the Enterprise plan. Ask an organization owner to change your plan."}
    </p>
  </section>
);

export default AutonomyLockCard;
