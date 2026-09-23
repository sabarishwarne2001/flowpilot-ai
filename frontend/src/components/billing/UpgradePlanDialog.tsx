import React from "react";
import { useNavigate } from "react-router-dom";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";

/** "Business and Enterprise", "Developer, Business and Enterprise". */
export const joinPlanNames = (names: readonly string[]): string => {
  if (names.length === 0) {
    return "a higher plan";
  }
  if (names.length === 1) {
    return `the ${names[0]} plan`;
  }
  return `the ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} plans`;
};

export interface UpgradePlanDialogProps {
  readonly open: boolean;
  readonly featureName: string;
  readonly includedIn: readonly string[];
  /** OWNER or BILLING: can open the plan picker. */
  readonly canChangePlan: boolean;
  readonly billingPath: string;
  readonly onClose: () => void;
}

/**
 * HM-S1:upgrade-dialog — what a locked sidebar row opens.
 *
 * A capability is bundled into a tier, so the remedy is a plan change and the
 * only action offered is the plan picker (for someone who can use it). The
 * plans named come from the server's published tiers, not from a copy here.
 */
export const UpgradePlanDialog: React.FC<UpgradePlanDialogProps> = ({
  open,
  featureName,
  includedIn,
  canChangePlan,
  billingPath,
  onClose,
}) => {
  const navigate = useNavigate();
  const plans = joinPlanNames(includedIn);
  return (
    <ConfirmDialog
      open={open}
      title={`${featureName} isn't included in your plan`}
      message={
        canChangePlan
          ? `${featureName} is included on ${plans}. Upgrade to turn it on for your whole organization.`
          : `${featureName} is included on ${plans}. Ask an organization owner to upgrade.`
      }
      confirmText={canChangePlan ? "View plans" : "Got it"}
      cancelText="Not now"
      tone="primary"
      initialFocus="confirm"
      hideCancel={!canChangePlan}
      onConfirm={() => {
        onClose();
        if (canChangePlan) {
          navigate(billingPath);
        }
      }}
      onCancel={onClose}
    />
  );
};

export default UpgradePlanDialog;
