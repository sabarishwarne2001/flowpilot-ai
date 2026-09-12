import React from "react";
import { AlertOctagon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { getBillingAccessSummary } from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { pollUnlessRefused } from "@/services/api/polling";
import { formatTimestampDate } from "@/utils/displayTime";

export interface MemberAccessNoticeProps {
  readonly organizationId: string;
  readonly className?: string;
}

/**
 * ARCH-30 Tranche 4 (A5) — what a plain member is told when the organization
 * is in grace or read-only.
 *
 * THE DEFECT THIS CLOSES
 * ----------------------
 * `DunningBanner` is only mounted for OWNER / ADMIN / BILLING, because
 * `GET /billing/access` is gated to those roles and mounting it for everybody
 * produced a wall of 403s. The consequence was that an ordinary member hit a
 * refused upload with no explanation anywhere on screen: the account was
 * read-only and the only people who could see that were the three roles least
 * likely to be uploading.
 *
 * WHY A SEPARATE COMPONENT AND A SEPARATE ENDPOINT
 * ------------------------------------------------
 * Not a `canManageBilling={false}` render of `DunningBanner`. That component
 * reads `BillingAccessResponse`, which carries dunning step history and
 * subscription status — commercial detail a member has no business seeing and
 * that the summary endpoint deliberately does not return. Reusing the
 * component would have meant either widening the member endpoint until it was
 * the billing endpoint again, or a component whose props promise fields the
 * member payload cannot supply.
 *
 * What a member gets is three facts: whether writes are paused, whether there
 * is a date before which they are not, and who to talk to. No amounts, no
 * invoices, no gateway state, and no button — a member cannot pay and offering
 * them a portal link that 403s is worse than offering nothing.
 *
 * ACTIVE renders nothing at all, which is the overwhelmingly common case.
 */
export const MemberAccessNotice: React.FC<MemberAccessNoticeProps> = ({
  organizationId,
  className = "",
}) => {
  const { data: summary } = useQuery({
    queryKey: billingKeys.accessSummary(organizationId),
    queryFn: () => getBillingAccessSummary(organizationId),
    enabled: Boolean(organizationId),
    refetchInterval: pollUnlessRefused(120_000),
    refetchOnWindowFocus: true,
    staleTime: 60_000,
    retry: false,
  });

  if (!summary || summary.state === "ACTIVE") {
    return null;
  }

  const restricted = summary.is_read_only;

  return (
    <div
      role={restricted ? "alert" : "status"}
      aria-live={restricted ? "assertive" : "polite"}
      className={
        restricted
          ? `border-b border-destructive/40 bg-destructive/10 px-4 py-3 ${className}`
          : `border-b border-amber-500/40 bg-amber-500/10 px-4 py-3 ${className}`
      }
    >
      <div className="mx-auto flex max-w-6xl items-start gap-3">
        <AlertOctagon
          className={`mt-0.5 h-5 w-5 shrink-0 ${
            restricted ? "text-destructive" : "text-amber-600"
          }`}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <p
            className={`text-sm font-semibold ${
              restricted ? "text-destructive" : "text-foreground"
            }`}
          >
            {restricted
              ? "This workspace is read-only right now"
              : "This workspace has a billing issue"}
          </p>
          <p className="mt-1 text-sm text-foreground/80">
            {restricted ? (
              <>
                You can still read, search, and export everything. New uploads,
                AI generation, and edits are paused until an owner or billing
                administrator resolves the account.
              </>
            ) : (
              <>
                Everything still works
                {summary.grace_ends_at
                  ? ` until ${formatTimestampDate(summary.grace_ends_at)}`
                  : " for now"}
                . An owner or billing administrator has been notified and needs
                to resolve the account before then.
              </>
            )}
          </p>
          <p className="mt-1.5 text-xs text-muted-foreground">
            Nothing has been deleted and export remains available.
          </p>
        </div>
      </div>
    </div>
  );
};

export default MemberAccessNotice;
