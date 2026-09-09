import React, { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, UserPlus } from "lucide-react";

import { listOrganizationMembers } from "@/services/api/organization";
import { grantWorkspaceAccess } from "@/services/api/workspaces";
import type {
  OrganizationRole,
  WorkspaceMember,
  WorkspaceRole,
} from "@/types/tenancy";

/**
 * ARCH-29 Slice 2 — grant an existing organization member access to a workspace.
 *
 * WHY THIS IS A PICKER AND NOT A USER-ID FIELD
 * ============================================
 *
 * `grantWorkspaceAccess` takes a bare `user_id`, and the cheap wiring is a text
 * input for a UUID. That input has exactly one correct value out of every
 * string a person can type, and every wrong one produces the same opaque 4xx.
 * The set of users who can legitimately be granted is knowable — an ACTIVE
 * organization member who is not already in this workspace — so the UI offers
 * that set and nothing else.
 *
 * THREE EXCLUSIONS, EACH FOR A DIFFERENT REASON
 * =============================================
 *
 * 1. Already a real member. Granting again is a duplicate row, not an edit.
 *    Change their role in the members table instead.
 *
 * 2. Already a DERIVED member (`is_derived`). Organization owners and admins
 *    reach every workspace through their organization standing; they have no
 *    workspace membership row. They are excluded but shown as such, because
 *    silently omitting the org owner from an "add member" list reads as a bug
 *    to the admin looking for them.
 *
 * 3. Not ACTIVE at the organization. The seat is what authorises presence in
 *    the tenant; the backend rejects the grant and it is better to not offer
 *    it than to explain the rejection afterwards.
 *
 * ADMIN is offered conditionally. The backend additionally requires
 * organization-level standing to grant workspace ADMIN, so an actor without it
 * sees the option disabled with the reason rather than a refusal on submit.
 */

const ROLE_OPTIONS: readonly {
  readonly value: WorkspaceRole;
  readonly label: string;
  readonly hint: string;
}[] = [
  {
    value: "VIEWER",
    label: "Viewer",
    hint: "Reads documents and conversations. Changes nothing.",
  },
  {
    value: "CONTRIBUTOR",
    label: "Contributor",
    hint: "Uploads documents, runs the assistant, edits work items.",
  },
  {
    value: "ADMIN",
    label: "Admin",
    hint: "Everything a contributor can do, plus workspace settings and members.",
  },
];

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail[0]?.msg) {
    return String(detail[0].msg);
  }
  return fallback;
}

export interface GrantWorkspaceAccessModalProps {
  readonly workspaceId: string;
  readonly organizationId: string;
  /** Current workspace members, real and derived, used to compute eligibility. */
  readonly currentMembers: readonly WorkspaceMember[];
  /** The acting user's organization role; gates whether ADMIN can be granted. */
  readonly actorOrganizationRole: OrganizationRole | null;
  readonly onClose: () => void;
  readonly onGranted: () => void;
}

export const GrantWorkspaceAccessModal: React.FC<
  GrantWorkspaceAccessModalProps
> = ({
  workspaceId,
  organizationId,
  currentMembers,
  actorOrganizationRole,
  onClose,
  onGranted,
}) => {
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("CONTRIBUTOR");
  const [error, setError] = useState<string | null>(null);

  const canGrantAdmin =
    actorOrganizationRole === "OWNER" || actorOrganizationRole === "ADMIN";

  const orgMembers = useQuery({
    queryKey: ["organizations", organizationId, "members", "active"],
    queryFn: () => listOrganizationMembers(organizationId, false),
    enabled: Boolean(organizationId),
  });

  const { eligible, derivedCount } = useMemo(() => {
    const existing = new Set(
      currentMembers
        .filter((member) => !member.is_derived)
        .map((member) => member.user.id),
    );
    const derived = new Set(
      currentMembers
        .filter((member) => member.is_derived)
        .map((member) => member.user.id),
    );

    const items = (orgMembers.data?.items ?? []).filter(
      (member) =>
        member.status === "ACTIVE" &&
        !existing.has(member.user.id) &&
        !derived.has(member.user.id),
    );

    return { eligible: items, derivedCount: derived.size };
  }, [orgMembers.data, currentMembers]);

  const grant = useMutation({
    mutationFn: () =>
      grantWorkspaceAccess(workspaceId, { user_id: userId, role }),
    onSuccess: () => {
      setError(null);
      onGranted();
      onClose();
    },
    onError: (err) =>
      setError(
        detailOf(
          err,
          "That grant was refused. The person may no longer hold an active seat in this organization.",
        ),
      ),
  });

  const ready = userId.length > 0 && !(role === "ADMIN" && !canGrantAdmin);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="grant-access-title"
    >
      <div className="w-full max-w-lg rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="flex items-start gap-3">
          <UserPlus
            className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <div>
            <h3
              id="grant-access-title"
              className="text-base font-semibold text-foreground"
            >
              Add someone to this workspace
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Only people who already have a seat in this organization can be
              added. To bring in someone new, invite them to the organization
              first.
            </p>
          </div>
        </div>

        <div className="mt-4 space-y-3">
          <div>
            <label
              htmlFor="grant-user"
              className="text-sm font-medium text-foreground"
            >
              Person
            </label>
            {orgMembers.isLoading ? (
              <p className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                Loading organization members…
              </p>
            ) : orgMembers.isError ? (
              <p className="mt-1 text-sm text-destructive" role="alert">
                {detailOf(
                  orgMembers.error,
                  "The organization member list could not be loaded.",
                )}
              </p>
            ) : eligible.length === 0 ? (
              <p className="mt-1 text-sm text-muted-foreground">
                Everyone with a seat in this organization already reaches this
                workspace.
              </p>
            ) : (
              <select
                id="grant-user"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={userId}
                onChange={(event) => setUserId(event.target.value)}
              >
                <option value="">Choose a person…</option>
                {eligible.map((member) => (
                  <option key={member.user.id} value={member.user.id}>
                    {member.user.email} · {member.role}
                  </option>
                ))}
              </select>
            )}
            {derivedCount > 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {derivedCount === 1
                  ? "One organization owner or admin is"
                  : `${derivedCount} organization owners or admins are`}{" "}
                not listed: they already reach every workspace through their
                organization role and need no grant here.
              </p>
            ) : null}
          </div>

          <fieldset>
            <legend className="text-sm font-medium text-foreground">
              Role in this workspace
            </legend>
            <div className="mt-1.5 space-y-1.5">
              {ROLE_OPTIONS.map((option) => {
                const blocked = option.value === "ADMIN" && !canGrantAdmin;
                return (
                  <label
                    key={option.value}
                    className={`flex items-start gap-2 rounded-md border p-2 text-sm ${
                      blocked
                        ? "border-border/60 opacity-60"
                        : "border-border cursor-pointer"
                    }`}
                  >
                    <input
                      type="radio"
                      name="grant-role"
                      className="mt-1"
                      value={option.value}
                      checked={role === option.value}
                      disabled={blocked}
                      onChange={() => setRole(option.value)}
                    />
                    <span>
                      <span className="font-medium text-foreground">
                        {option.label}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {blocked
                          ? "Only an organization owner or admin can grant workspace admin."
                          : option.hint}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          {error ? (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-3 py-1.5 text-sm"
            onClick={onClose}
            disabled={grant.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            disabled={!ready || grant.isPending}
            onClick={() => grant.mutate()}
          >
            {grant.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : null}
            Add to workspace
          </button>
        </div>
      </div>
    </div>
  );
};

export default GrantWorkspaceAccessModal;
