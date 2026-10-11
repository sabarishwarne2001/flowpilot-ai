import React from "react";
import { useQuery } from "@tanstack/react-query";

import { useTenant } from "@/hooks/useTenant";
import { getBillingAccessSummary } from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";

export interface AskPlanOwnersProps {
  /** What to ask for, completing "Ask Ada Owner to …": "change the plan", "add it". */
  readonly toDo?: string;
  readonly organizationId?: string;
  readonly className?: string;
}

/**
 * Campaign session 1 (E.4). Every "ask someone" line names who to ask.
 *
 * "Ask an organization owner" left a member to work out who that is. The
 * member-readable billing summary carries the names of the people who can change
 * the plan (the owners: only they can); this line reads them, and falls back
 * to the generic wording only while they load or if the organization has none.
 */
export const AskPlanOwners: React.FC<AskPlanOwnersProps> = ({
  toDo = "change the plan",
  organizationId,
  className = "",
}) => {
  const sentence = useAskPlanOwners(toDo, organizationId);
  return <span className={className} data-testid="ask-plan-owners">{sentence}</span>;
};

/** The same sentence as a string, for places that take text (a dialog's message). */
export const useAskPlanOwners = (toDo = "change the plan", organizationId?: string): string => {
  const { state } = useTenant();
  const orgId = organizationId ?? (state.status === "ready" ? state.organization.organization_id : undefined);
  const { data } = useQuery({
    queryKey: billingKeys.accessSummary(orgId ?? ""),
    queryFn: () => getBillingAccessSummary(orgId!),
    enabled: Boolean(orgId),
    staleTime: 60_000,
    retry: false,
  });
  const names = data?.plan_contacts ?? [];
  const who =
    names.length === 0
      ? "an organization owner"
      : names.length === 1
        ? names[0]
        : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
  return `Ask ${who} to ${toDo}.`;
};

export default AskPlanOwners;
