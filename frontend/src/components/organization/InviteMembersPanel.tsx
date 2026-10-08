/**
 * N-020 item 5 — invite people from Organization → Members.
 *
 * Invitations were only reachable from one workspace's settings, which made
 * "add a BILLING person with no workspace" or "add someone to three
 * workspaces" a detour. The organization invitation API already takes an
 * organization role and any number of workspace grants; this is its form,
 * plus the pending list with Resend and Revoke.
 *
 * OWNER is not invitable (ownership moves through the two-party transfer);
 * an OWNER may invite ADMIN, BILLING and MEMBER, an ADMIN only BILLING and
 * MEMBER, as the server enforces, and the form offers only those (F-139). A
 * pending invitation holds a seat, so the plan's seat limit is checked when
 * it is sent and the server's refusal is shown as it is worded.
 */

import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, MailPlus, RotateCw, XCircle } from "lucide-react";

import {
  createInvitation,
  listPendingInvitations,
  resendInvitation,
  revokeInvitation,
} from "@/services/api/invitations";
import { listOrganizationWorkspaces } from "@/services/api/organization";
import { ApiError } from "@/services/api/client";
import { formatTimestampDate } from "@/utils/displayTime";
import { canAssignOrganizationRole } from "@/permissions/organizationPermissions";
import type { OrganizationRole, WorkspaceRole } from "@/types/tenancy";

interface PendingInvitation {
  readonly id: string;
  readonly email: string;
  readonly organization_role: OrganizationRole;
  readonly status: string;
  readonly expires_at: string;
  readonly send_count?: number;
  readonly grants?: ReadonlyArray<{ readonly workspace_id: string; readonly workspace_name: string; readonly role: WorkspaceRole }>;
}

const INVITABLE: readonly OrganizationRole[] = ["MEMBER", "ADMIN", "BILLING"];
const WORKSPACE_ROLES: readonly WorkspaceRole[] = ["VIEWER", "CONTRIBUTOR", "ADMIN"];
const ROLE_WORDS: Readonly<Record<string, string>> = {
  MEMBER: "Member",
  ADMIN: "Admin",
  BILLING: "Billing",
  OWNER: "Owner",
  VIEWER: "Viewer",
  CONTRIBUTOR: "Contributor",
};

const message = (error: unknown, fallback: string): string =>
  error instanceof ApiError && error.message ? error.message : fallback;

export const invitationKeys = {
  pending: (organizationId: string) => ["organizations", "invitations", organizationId] as const,
};

export const InviteMembersPanel: React.FC<{
  readonly organizationId: string;
  /** The inviter's own role: only the roles it may grant are offered (F-139). */
  readonly actorRole: OrganizationRole;
}> = ({ organizationId, actorRole }) => {
  const queryClient = useQueryClient();
  // The server's rule (an admin cannot create a peer admin). Offering every role let an admin
  // fill the form in for "Admin" and be refused only on submit.
  const invitable = useMemo(
    () => INVITABLE.filter((candidate) => canAssignOrganizationRole(actorRole, candidate)),
    [actorRole],
  );
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrganizationRole>("MEMBER");
  const [grants, setGrants] = useState<Record<string, WorkspaceRole>>({});

  const workspaces = useQuery({
    queryKey: ["organizations", organizationId, "workspaces-for-invite"],
    queryFn: () => listOrganizationWorkspaces(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });
  const pending = useQuery({
    queryKey: invitationKeys.pending(organizationId),
    queryFn: () => listPendingInvitations(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 15_000,
  });
  const invitations = useMemo<PendingInvitation[]>(() => {
    const raw = pending.data as unknown;
    const rows = Array.isArray(raw) ? raw : ((raw as { items?: unknown[] } | undefined)?.items ?? []);
    return (rows as PendingInvitation[]).filter((row) => row.status === "PENDING");
  }, [pending.data]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: invitationKeys.pending(organizationId) });

  const send = useMutation({
    mutationFn: () =>
      createInvitation(organizationId, {
        email: email.trim(),
        organization_role: role,
        grants: Object.entries(grants).map(([workspace_id, workspaceRole]) => ({ workspace_id, role: workspaceRole })),
      }),
    onSuccess: async () => {
      toast.success(`Invitation sent to ${email.trim()}.`);
      setEmail("");
      setRole("MEMBER");
      setGrants({});
      await refresh();
    },
    onError: (error) => toast.error(message(error, "The invitation couldn't be sent.")),
  });
  const resend = useMutation({
    mutationFn: (id: string) => resendInvitation(organizationId, id),
    onSuccess: async () => {
      toast.success("Invitation sent again.");
      await refresh();
    },
    onError: (error) => toast.error(message(error, "The invitation couldn't be resent.")),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => revokeInvitation(organizationId, id),
    onSuccess: async () => {
      toast.success("Invitation revoked.");
      await refresh();
    },
    onError: (error) => toast.error(message(error, "The invitation couldn't be revoked.")),
  });

  const validEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

  return (
    <section aria-labelledby="invite-heading" className="space-y-4 rounded-lg border border-border bg-card p-4">
      <header>
        <h2 id="invite-heading" className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <MailPlus className="h-4 w-4" aria-hidden />
          Invite people
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          They get an email with a link that expires in 7 days. A pending invitation holds a seat.
        </p>
      </header>

      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (validEmail) {send.mutate();}
        }}
      >
        <div className="flex flex-wrap gap-3">
          <label className="min-w-[220px] flex-1 text-xs font-medium text-muted-foreground">
            Email address
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="name@company.com"
              autoComplete="off"
              className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground"
            />
          </label>
          <label className="text-xs font-medium text-muted-foreground">
            Organization role
            <select
              value={role}
              onChange={(event) => setRole(event.target.value as OrganizationRole)}
              className="mt-1 block rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground"
            >
              {invitable.map((value) => (
                <option key={value} value={value}>
                  {ROLE_WORDS[value]}
                </option>
              ))}
            </select>
          </label>
        </div>

        <fieldset className="rounded-md border border-border p-3">
          <legend className="px-1 text-xs font-medium text-muted-foreground">Workspace access (optional)</legend>
          {workspaces.isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : (workspaces.data ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">No workspaces yet.</p>
          ) : (
            <ul className="space-y-2">
              {(workspaces.data ?? []).map((workspace) => {
                const chosen = grants[workspace.id];
                return (
                  <li key={workspace.id} className="flex flex-wrap items-center gap-3">
                    <label className="flex min-w-[180px] flex-1 items-center gap-2 text-sm text-foreground">
                      <input
                        type="checkbox"
                        checked={chosen !== undefined}
                        onChange={(event) =>
                          setGrants((current) => {
                            const next = { ...current };
                            if (event.target.checked) {
                              next[workspace.id] = "VIEWER";
                            } else {
                              delete next[workspace.id];
                            }
                            return next;
                          })
                        }
                      />
                      {workspace.workspace_name}
                    </label>
                    {chosen !== undefined && (
                      <select
                        aria-label={`Role in ${workspace.workspace_name}`}
                        value={chosen}
                        onChange={(event) =>
                          setGrants((current) => ({ ...current, [workspace.id]: event.target.value as WorkspaceRole }))
                        }
                        className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                      >
                        {WORKSPACE_ROLES.map((value) => (
                          <option key={value} value={value}>
                            {ROLE_WORDS[value]}
                          </option>
                        ))}
                      </select>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {role === "BILLING" && (
            <p className="mt-2 text-xs text-muted-foreground">
              Billing people usually need no workspace: they see the plan, invoices and usage.
            </p>
          )}
        </fieldset>

        <button
          type="submit"
          disabled={!validEmail || send.isPending}
          className="fp-btn-primary inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
        >
          {send.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MailPlus className="h-3.5 w-3.5" />}
          Send invitation
        </button>
      </form>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Pending invitations{invitations.length ? ` (${invitations.length})` : ""}
        </h3>
        {invitations.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">No invitations are waiting for an answer.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border rounded-md border border-border" aria-label="Pending invitations">
            {invitations.map((invitation) => {
              // The link stops working at expires_at (accepting it is refused); the row says so
              // instead of still reading as pending. Resend issues a fresh link and a new expiry.
              const expired = new Date(invitation.expires_at).getTime() <= Date.now();
              return (
              <li key={invitation.id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 truncate text-sm font-medium text-foreground">
                    <span className="truncate">{invitation.email}</span>
                    {expired && (
                      <span className="shrink-0 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                        Expired
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {ROLE_WORDS[invitation.organization_role] ?? invitation.organization_role}
                    {invitation.grants && invitation.grants.length > 0
                      ? ` · ${invitation.grants.map((g) => `${g.workspace_name} (${ROLE_WORDS[g.role] ?? g.role})`).join(", ")}`
                      : ""}
                    {expired
                      ? ` · expired ${formatTimestampDate(invitation.expires_at)}; resend to renew it`
                      : ` · expires ${formatTimestampDate(invitation.expires_at)}`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => resend.mutate(invitation.id)}
                  disabled={resend.isPending}
                  aria-label={`Resend the invitation to ${invitation.email}`}
                  className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs hover:bg-muted disabled:opacity-60"
                >
                  <RotateCw className="h-3 w-3" aria-hidden /> Resend
                </button>
                <button
                  type="button"
                  onClick={() => revoke.mutate(invitation.id)}
                  disabled={revoke.isPending}
                  aria-label={`Revoke the invitation to ${invitation.email}`}
                  className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-60"
                >
                  <XCircle className="h-3 w-3" aria-hidden /> Revoke
                </button>
              </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
};

export default InviteMembersPanel;
