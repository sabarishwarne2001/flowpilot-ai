import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, ShieldCheck } from "lucide-react";

import { errorMessage } from "@/services/api/errors";
import { getTwoFactorPolicy, setTwoFactorPolicy } from "@/services/api/organization";
import { organizationKeys } from "@/services/api/queryKeys";

/**
 * Campaign session 3 (N-017 decided again). An organization can require two-factor sign-in.
 *
 * Owners turn it on; admins see it. Before turning it on the owner is shown exactly who will be
 * asked to set it up at their next click (the server answers 403 ORGANIZATION_MFA_REQUIRED to a
 * password session without a second factor). People who sign in through the organization's
 * identity provider are not asked: the provider is where their MFA lives. The server refuses to
 * turn it on for an owner who has no second factor themselves, so this switch can never lock out
 * the person who flips it.
 */
export const TwoFactorRequirementCard: React.FC<{
  readonly organizationId: string;
  readonly isOwner: boolean;
}> = ({ organizationId, isOwner }) => {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const query = useQuery({
    queryKey: organizationKeys.twoFactorPolicy(organizationId),
    queryFn: () => getTwoFactorPolicy(organizationId),
    staleTime: 30_000,
  });
  const update = useMutation({
    mutationFn: (required: boolean) => setTwoFactorPolicy(organizationId, required),
    onSuccess: (policy) => {
      queryClient.setQueryData(organizationKeys.twoFactorPolicy(organizationId), policy);
      setConfirming(false);
    },
  });

  const policy = query.data;
  const missing = policy?.members_without_two_factor ?? [];

  return (
    <section className="space-y-4 rounded-xl border border-border/60 bg-card/50 p-6 backdrop-blur-sm">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted/40">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
        </span>
        <div className="min-w-0 space-y-0.5">
          <h2 className="text-sm font-semibold text-foreground">Two-factor sign-in</h2>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Require everyone who signs in with a password to use an authenticator app as well.
            People who sign in through your identity provider use its own two-factor check.
          </p>
        </div>
      </div>

      {query.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading…
        </p>
      ) : query.isError || !policy ? (
        <p role="alert" className="text-sm text-destructive">
          {errorMessage(query.error, "The two-factor setting could not be loaded.")}
        </p>
      ) : (
        <>
          <p className="text-sm text-foreground">
            {policy.required ? "Required for every member." : "Not required."}{" "}
            {missing.length === 0
              ? "Every member has two-factor sign-in on."
              : `${missing.length} ${missing.length === 1 ? "member has" : "members have"} not turned it on yet.`}
          </p>
          {missing.length > 0 ? (
            <ul aria-label="Members without two-factor sign-in" className="max-h-40 space-y-1 overflow-y-auto text-xs text-muted-foreground">
              {missing.map((member) => (
                <li key={member.user_id}>
                  {member.display_name ? `${member.display_name} · ` : ""}
                  {member.email}
                </li>
              ))}
            </ul>
          ) : null}

          {isOwner ? (
            confirming ? (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                <p className="flex items-start gap-1.5 text-xs text-foreground">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" aria-hidden="true" />
                  <span>
                    {missing.length === 0
                      ? "Everyone already uses two-factor sign-in; nobody is interrupted."
                      : `The ${missing.length} ${missing.length === 1 ? "member" : "members"} listed above will be asked to turn it on before they can continue.`}
                  </span>
                </p>
                <div className="mt-2 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    className="rounded border border-border px-2.5 py-1 text-xs hover:bg-muted"
                  >
                    Not now
                  </button>
                  <button
                    type="button"
                    onClick={() => update.mutate(true)}
                    disabled={update.isPending}
                    className="rounded bg-primary px-2.5 py-1 text-xs text-primary-foreground disabled:opacity-50"
                  >
                    Require two-factor sign-in
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => (policy.required ? update.mutate(false) : setConfirming(true))}
                  disabled={update.isPending}
                  className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
                >
                  {policy.required ? "Stop requiring two-factor sign-in" : "Require two-factor sign-in…"}
                </button>
              </div>
            )
          ) : (
            <p className="text-xs text-muted-foreground">Only an owner can change this.</p>
          )}
          {update.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(update.error, "That change was not saved.")}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
};
