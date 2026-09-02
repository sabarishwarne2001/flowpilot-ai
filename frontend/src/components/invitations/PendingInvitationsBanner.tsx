import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Mail } from "lucide-react";

import { listMyInvitations } from "@/services/api/invitations";
import { meInvitationKeys } from "@/services/api/queryKeys";

export const PendingInvitationsBanner: React.FC = () => {
  const { data } = useQuery({
    queryKey: meInvitationKeys.list(),
    queryFn: listMyInvitations,
    staleTime: 5 * 60_000,
  });

  const invitations = data?.items ?? [];
  if (invitations.length === 0) {return null;}

  return (
    <div className="border-b border-border bg-muted/40">
      {invitations.map((invitation) => (
        <div
          key={`${invitation.organization_name}-${invitation.expires_at}`}
          className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3"
        >
          <Mail className="h-5 w-5 flex-shrink-0 text-muted-foreground" />

          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">
              <strong>{invitation.inviter_email}</strong> invited you to{" "}
              <strong>{invitation.organization_name}</strong> as{" "}
              {invitation.organization_role.toLowerCase()}.
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {invitation.workspaces.length > 0 && (
                <>
                  Includes access to{" "}
                  {invitation.workspaces
                    .map((w) => `${w.name} (${w.role.toLowerCase()})`)
                    .join(", ")}
                  {" · "}
                </>
              )}
              Expires {new Date(invitation.expires_at).toLocaleDateString()}.
              Open the link in your invitation email to accept.
            </p>
          </div>
        </div>
      ))}
    </div>
  );
};

export default PendingInvitationsBanner;
