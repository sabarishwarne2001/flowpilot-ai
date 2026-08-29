import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, ShieldAlert, UserMinus, Users } from "lucide-react";

import {
  changeOrganizationMemberRole,
  deactivateOrganizationMember,
  listOrganizationMembers,
} from "@/services/api/organization";
import { organizationKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  ADMIN_ASSIGNABLE_ROLES,
  canAssignOrganizationRole,
  canManageMembers,
  canModifyMember,
  canModifyMemberRole,
} from "@/permissions/organizationPermissions";
import type { OrganizationMember, OrganizationRole } from "@/types/tenancy";
import OwnershipTransferPanel from "@/components/organization/OwnershipTransferPanel";

const ALL_ROLES: readonly OrganizationRole[] = [
  "OWNER",
  "ADMIN",
  "BILLING",
  "MEMBER",
] as const;

const ROLE_BLURB: Readonly<Record<OrganizationRole, string>> = {
  OWNER: "Full control, including billing and ownership transfer.",
  ADMIN: "Manages members and workspaces. Cannot transfer ownership.",
  BILLING: "Sees invoices, plan, and usage. No access to documents.",
  MEMBER: "Access to the workspaces they have been added to.",
};

export const OrganizationMembers: React.FC = () => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();
  const queryClient = useQueryClient();

  const actorRole = String(organizationRole).toUpperCase() as OrganizationRole;
  const canManage = canManageMembers(actorRole);

  const [includeInactive, setIncludeInactive] = useState(false);
  const [confirmingRemoval, setConfirmingRemoval] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const {
    data,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: organizationKeys.members(organizationId, includeInactive),
    queryFn: () => listOrganizationMembers(organizationId, includeInactive),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: organizationKeys.all(organizationId),
    });

  const roleChange = useMutation({
    mutationFn: ({
      membershipId,
      role,
    }: {
      membershipId: string;
      role: OrganizationRole;
    }) => changeOrganizationMemberRole(organizationId, membershipId, { role }),
    onSuccess: () => {
      setActionError(null);
      void invalidate();
    },
    onError: () =>
      setActionError(
        "That role change was refused. You may not have permission, or the " +
          "organization must keep at least one owner.",
      ),
  });

  const removal = useMutation({
    mutationFn: (membershipId: string) =>
      deactivateOrganizationMember(organizationId, membershipId),
    onSuccess: () => {
      setActionError(null);
      setConfirmingRemoval(null);
      void invalidate();
    },
    onError: () =>
      setActionError(
        "That member couldn't be removed. An organization must keep at least " +
          "one owner, and you cannot remove someone at or above your own role.",
      ),
  });

  const members = data?.items ?? [];

  const { active, inactive } = useMemo(() => {
    const isActive = (m: OrganizationMember) => m.status === "ACTIVE";
    return {
      active: members.filter(isActive),
      inactive: members.filter((m) => !isActive(m)),
    };
  }, [members]);

  const assignableFor = (member: OrganizationMember): OrganizationRole[] => {
    if (!canModifyMember(actorRole, member.role)) {
      return [];
    }
    // Ownership changes occur exclusively through the two-party OwnershipTransferPanel
    const candidates =
      actorRole === "OWNER"
        ? ALL_ROLES.filter((r) => r !== "OWNER")
        : [...ADMIN_ASSIGNABLE_ROLES];
    return candidates.filter(
      (role) =>
        role === member.role ||
        (canAssignOrganizationRole(actorRole, role) &&
          canModifyMemberRole(actorRole, member.role, role)),
    );
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading members…
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <p role="alert" className="text-sm text-destructive">
          The member directory couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
        <header>
          <h1 className="text-xl font-semibold text-foreground">Members</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {organization.organization_name}
          </p>
        </header>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
          <div className="flex items-center gap-2 text-sm">
            <Users className="h-4 w-4 text-muted-foreground" />
            <span>
              <strong>{data?.total ?? 0}</strong>{" "}
              {data?.total === 1 ? "member" : "members"}
            </span>
            <span className="text-muted-foreground">
              · {data?.seats_consumed ?? 0} seats in use
            </span>
          </div>

          {canManage && (
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(event) => setIncludeInactive(event.target.checked)}
              />
              Show removed members
            </label>
          )}
        </div>

        <p className="text-xs text-muted-foreground">
          Seats include pending invitations — an unaccepted invitation holds a
          seat so an organization cannot invite past its plan limit.
        </p>

        {actionError && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {actionError}
          </p>
        )}

        {members.length === 0 ? (
          <div className="rounded-lg border border-border bg-card p-6 text-center">
            <AlertTriangle className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium text-foreground">No members yet</p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Invite people from a workspace&apos;s settings — an invitation
              grants membership to this organization.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border bg-card">
            {[...active, ...inactive].map((member) => {
              const assignable = assignableFor(member);
              const canRemove =
                canModifyMember(actorRole, member.role) &&
                member.status === "ACTIVE";
              const isRemoved = member.status !== "ACTIVE";

              return (
                <li
                  key={member.id}
                  className={`flex flex-wrap items-center gap-3 p-4 ${
                    isRemoved ? "opacity-60" : ""
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">
                      {member.user.email}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {member.status.toLowerCase()} member
                    </p>
                    {isRemoved && (
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Removed
                        {member.deactivated_at
                          ? ` ${new Date(member.deactivated_at).toLocaleDateString()}`
                          : ""}
                        {" — retained for attribution"}
                      </p>
                    )}
                  </div>

                  {assignable.length > 1 && !isRemoved ? (
                    <select
                      value={member.role}
                      onChange={(event) =>
                        roleChange.mutate({
                          membershipId: member.id,
                          role: event.target.value as OrganizationRole,
                        })
                      }
                      disabled={roleChange.isPending}
                      aria-label={`Role for ${member.user.email}`}
                      className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground disabled:opacity-60"
                    >
                      {assignable.map((role) => (
                        <option key={role} value={role} title={ROLE_BLURB[role]}>
                          {role}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span
                      title={ROLE_BLURB[member.role]}
                      className="rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-foreground"
                    >
                      {member.role}
                    </span>
                  )}

                  {canRemove &&
                    (confirmingRemoval === member.id ? (
                      <span className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => removal.mutate(member.id)}
                          disabled={removal.isPending}
                          className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground disabled:opacity-60"
                        >
                          {removal.isPending && (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          )}
                          Confirm removal
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmingRemoval(null)}
                          disabled={removal.isPending}
                          className="rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground disabled:opacity-60"
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => {
                          setActionError(null);
                          setConfirmingRemoval(member.id);
                        }}
                        aria-label={`Remove ${member.user.email}`}
                        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-foreground hover:bg-muted"
                      >
                        <UserMinus className="h-3 w-3" />
                        Remove
                      </button>
                    ))}
                </li>
              );
            })}
          </ul>
        )}

        {/* Ownership Transfer Panel */}
        <OwnershipTransferPanel
          organizationId={organizationId}
          members={members}
          isOwner={actorRole === "OWNER"}
        />

        {!canManage && (
          <p className="border-t border-border pt-4 text-xs text-muted-foreground">
            You can see who belongs to this organization. Changing roles or
            removing members requires an owner or administrator.
          </p>
        )}
      </div>
    </div>
  );
};

export default OrganizationMembers;
