import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crown, Loader2 } from "lucide-react";

import {
  acceptOwnershipTransfer,
  declineOwnershipTransfer,
  listMyOwnershipTransfers,
} from "@/services/api/ownership";
import { ownershipKeys } from "@/services/api/queryKeys";
import { useTenant } from "@/hooks/useTenant";
import type { OrganizationMembershipSummary } from "@/types/tenancy";

export const IncomingOwnershipBanner: React.FC = () => {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ownershipKeys.mine,
    queryFn: listMyOwnershipTransfers,
    staleTime: 60_000,
  });

  const { state } = useTenant();
  const organizations = useMemo<OrganizationMembershipSummary[]>(
    () =>
      state.status === "ready" || state.status === "no_workspace"
        ? state.organizations
        : [],
    [state],
  );

  const nameFor = (organizationId: string): string =>
    organizations.find((o) => o.organization_id === organizationId)
      ?.organization_name ?? "an organization";

  const settled = () => {
    setConfirming(null);
    setError(null);
    void queryClient.invalidateQueries();
  };

  const accept = useMutation({
    mutationFn: (transfer: { id: string; organization_id: string }) =>
      acceptOwnershipTransfer(transfer.organization_id, transfer.id),
    onSuccess: settled,
    onError: () =>
      setError("That transfer couldn't be accepted. It may have expired or been withdrawn."),
  });

  const decline = useMutation({
    mutationFn: (transfer: { id: string; organization_id: string }) =>
      declineOwnershipTransfer(transfer.organization_id, transfer.id),
    onSuccess: settled,
    onError: () => setError("That transfer couldn't be declined."),
  });

  const pending = (data?.transfers ?? []).filter(
    (t) => t.status === "PENDING" && t.responded_at === null,
  );

  if (pending.length === 0) return null;

  return (
    <div className="border-b border-border bg-primary/10">
      {pending.map((transfer) => {
        const isConfirming = confirming === transfer.id;
        const busy =
          (accept.isPending && accept.variables?.id === transfer.id) ||
          (decline.isPending && decline.variables?.id === transfer.id);

        return (
          <div
            key={transfer.id}
            className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3"
          >
            <Crown className="h-5 w-5 flex-shrink-0 text-primary" />

            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">
                You&apos;ve been asked to take ownership of{" "}
                <strong>{nameFor(transfer.organization_id)}</strong>.
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {isConfirming
                  ? "Accepting gives you full control of this organization, including responsibility for its billing. The current owner becomes an administrator."
                  : `Expires ${new Date(transfer.expires_at).toLocaleDateString()}.`}
              </p>
              {error && (
                <p role="alert" className="mt-0.5 text-xs text-destructive">
                  {error}
                </p>
              )}
            </div>

            <div className="flex flex-shrink-0 flex-wrap gap-2">
              {isConfirming ? (
                <>
                  <button
                    type="button"
                    onClick={() => accept.mutate(transfer)}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60"
                  >
                    {busy && <Loader2 className="h-3 w-3 animate-spin" />}
                    Yes, take ownership
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(null)}
                    disabled={busy}
                    className="rounded-md border border-border bg-background px-3 py-1.5 text-xs text-foreground disabled:opacity-60"
                  >
                    Not yet
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setError(null);
                      setConfirming(transfer.id);
                    }}
                    className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90"
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    onClick={() => decline.mutate(transfer)}
                    disabled={busy}
                    className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-60"
                  >
                    Decline
                  </button>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default IncomingOwnershipBanner;
