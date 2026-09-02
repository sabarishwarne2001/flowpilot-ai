import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crown, Loader2, ShieldAlert } from "lucide-react";

import {
  cancelOwnershipTransfer,
  initiateOwnershipTransfer,
  listMyOwnershipTransfers,
} from "@/services/api/ownership";
import { ownershipKeys } from "@/services/api/queryKeys";
import type { OrganizationMember } from "@/types/tenancy";

interface Props {
  readonly organizationId: string;
  readonly members: readonly OrganizationMember[];
  readonly isOwner: boolean;
}

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

export const OwnershipTransferPanel: React.FC<Props> = ({
  organizationId,
  members,
  isOwner,
}) => {
  const queryClient = useQueryClient();

  const [proposing, setProposing] = useState(false);
  const [targetId, setTargetId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ownershipKeys.mine,
    queryFn: listMyOwnershipTransfers,
    staleTime: 30_000,
  });

  const outgoing = useMemo(
    () =>
      (data?.transfers ?? []).filter(
        (t) => t.organization_id === organizationId && t.status === "PENDING",
      ),
    [data, organizationId],
  );

  const candidates = useMemo(
    () =>
      members.filter((m) => m.status === "ACTIVE" && m.role !== "OWNER"),
    [members],
  );

  const nameFor = (membershipId: string): string => {
    const member = members.find((m) => m.id === membershipId);
    return member ? member.user.email : "a member";
  };

  const reset = () => {
    setProposing(false);
    setTargetId("");
    setPassword("");
  };

  const propose = useMutation({
    mutationFn: () =>
      initiateOwnershipTransfer(organizationId, {
        target_membership_id: targetId,
        current_password: password,
      }),
    onSuccess: () => {
      setError(null);
      reset();
      void queryClient.invalidateQueries({ queryKey: ownershipKeys.mine });
    },
    onError: (err) => {
      setPassword("");
      setError(
        detailOf(
          err,
          "That transfer couldn't be proposed. Check your password and that the member is active and verified.",
        ),
      );
    },
  });

  const withdraw = useMutation({
    mutationFn: (transferId: string) =>
      cancelOwnershipTransfer(organizationId, transferId),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ownershipKeys.mine });
    },
    onError: (err) =>
      setError(detailOf(err, "That proposal couldn't be withdrawn.")),
  });

  if (!isOwner) {return null;}

  return (
    <section className="rounded-lg border border-border bg-card">
      <header className="border-b border-border px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Crown className="h-4 w-4 text-primary" />
          Organization ownership
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Transferring ownership hands over full control, including billing.
          The person you choose has to accept before anything changes.
        </p>
      </header>

      <div className="space-y-4 p-4">
        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {error}
          </p>
        )}

        {outgoing.length > 0 ? (
          <ul className="space-y-2">
            {outgoing.map((transfer) => (
              <li
                key={transfer.id}
                className="flex flex-wrap items-center gap-3 rounded-md border border-border p-3 bg-background"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-foreground">
                    Waiting for{" "}
                    <strong>{nameFor(transfer.target_membership_id)}</strong> to
                    accept.
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Proposed{" "}
                    {new Date(transfer.created_at).toLocaleDateString()} ·
                    expires {new Date(transfer.expires_at).toLocaleDateString()}
                    . You are still the owner until they accept.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => withdraw.mutate(transfer.id)}
                  disabled={withdraw.isPending}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground hover:bg-muted disabled:opacity-60"
                >
                  {withdraw.isPending && (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  )}
                  Withdraw
                </button>
              </li>
            ))}
          </ul>
        ) : proposing ? (
          <div className="space-y-3">
            <div>
              <label htmlFor="transfer-target" className="text-sm font-medium text-foreground">
                New owner
              </label>
              <select
                id="transfer-target"
                value={targetId}
                onChange={(event) => setTargetId(event.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
              >
                <option value="">Select a member…</option>
                {candidates.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.user.email} ({member.role})
                  </option>
                ))}
              </select>
              {candidates.length === 0 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  No eligible members. Ownership can only pass to an active
                  member of this organization.
                </p>
              )}
            </div>

            <div className="rounded-md border border-border bg-muted/30 p-3">
              <p className="text-sm font-medium text-foreground">What happens on acceptance</p>
              <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                <li>· They become the owner, with full control over billing.</li>
                <li>· You are demoted to administrator.</li>
                <li>· Nothing changes until they accept — you can withdraw before then.</li>
                <li>· The proposal lapses if they don&apos;t respond in time.</li>
              </ul>
            </div>

            <div>
              <label htmlFor="transfer-password" className="text-sm font-medium text-foreground">
                Confirm your password
              </label>
              <input
                id="transfer-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                maxLength={128}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Required. Being signed in isn&apos;t enough to hand over an
                organization.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => propose.mutate()}
                disabled={
                  propose.isPending || !targetId || password.length === 0
                }
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {propose.isPending && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                )}
                Propose transfer
              </button>
              <button
                type="button"
                onClick={reset}
                disabled={propose.isPending}
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => {
              setError(null);
              setProposing(true);
            }}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium text-foreground hover:bg-muted"
          >
            Transfer ownership…
          </button>
        )}
      </div>
    </section>
  );
};

export default OwnershipTransferPanel;
