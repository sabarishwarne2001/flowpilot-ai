import React from "react";
import { Ban, Bell, CalendarClock, CircleCheck, FileQuestion, Gavel, HandCoins, Mail, PauseCircle, PlayCircle, Scale, type LucideIcon } from "lucide-react";

import type { ActionKind } from "@/types/helm";

const ICONS: Partial<Record<ActionKind, LucideIcon>> = {
  schedule_payment: HandCoins,
  approve_payment: HandCoins,
  hold_payment: PauseCircle,
  release_hold: PlayCircle,
  send_reminder: Mail,
  request_document: FileQuestion,
  decide_renewal: Gavel,
  create_task: CalendarClock,
  draft_dispute: Ban,
  draft_reconciliation: Scale,
  mark_paid: CircleCheck,
  notify: Bell,
  webhook: Bell,
};

const TONES: Partial<Record<ActionKind, string>> = {
  schedule_payment: "holding",
  approve_payment: "holding",
  mark_paid: "holding",
  hold_payment: "drifting",
  draft_dispute: "breached",
  send_reminder: "sky",
  request_document: "sky",
  draft_reconciliation: "sky",
};

/**
 * The plan's verb as a chip with its icon (docs/helm/VISUAL.md "VerbChip"): Pay, Hold, Chase,
 * Request, Reconcile, Decide. The word is the meaning; icon and tint help the eye scan the plan.
 */
export const VerbChip: React.FC<{ readonly kind: ActionKind; readonly verb: string }> = ({ kind, verb }) => {
  const Icon = ICONS[kind] ?? CalendarClock;
  const tone = TONES[kind] ?? "unknown";
  return (
    <span className={`helm-chip helm-tone-${tone} gap-1.5 px-2.5 font-semibold`} data-verb={verb}>
      <Icon className={`helm-state-${tone} h-3.5 w-3.5`} aria-hidden="true" />
      {verb}
    </span>
  );
};

export default VerbChip;
