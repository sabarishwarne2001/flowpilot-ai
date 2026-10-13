/**
 * Campaign session 3 (HANDOFF carry-over: access screens name people).
 *
 * "Ask a workspace administrator" or "contact an organization owner" left a
 * member to work out who that is. Every member may read the organization's
 * directory and the people of a workspace they are in, so the access screens
 * can name the people who can actually grant what is missing: a workspace's
 * administrators (stored grants and the organization owners and admins who
 * administer it by role), or an organization's owners and administrators.
 *
 * Returns at most three names (display name, else email), never the viewer;
 * empty while loading or if nobody qualifies, so callers keep their generic
 * wording as the fallback.
 */

import { useQuery } from "@tanstack/react-query";

import { listOrganizationMembers } from "@/services/api/organization";
import { organizationKeys } from "@/services/api/queryKeys";
import { listWorkspaceMembers } from "@/services/api/workspaces";
import { useAuthStore } from "@/store/useAuthStore";
import type { OrganizationRole, UserSummary } from "@/types/tenancy";

export type AccessContactsScope =
  | { readonly workspaceId: string }
  | { readonly organizationId: string; readonly roles: readonly OrganizationRole[] };

const MAX_NAMES = 3;

const nameOf = (user: UserSummary): string => user.display_name?.trim() || user.email;

export const useAccessContacts = (scope: AccessContactsScope | null): readonly string[] => {
  const selfId = useAuthStore((state) => state.user?.id ?? null);
  const workspaceId = scope && "workspaceId" in scope ? scope.workspaceId : null;
  const organizationId = scope && "organizationId" in scope ? scope.organizationId : null;

  const workspace = useQuery({
    queryKey: ["workspaces", "members", workspaceId],
    queryFn: () => listWorkspaceMembers(workspaceId!),
    enabled: Boolean(workspaceId),
    staleTime: 60_000,
    retry: false,
  });
  const organization = useQuery({
    queryKey: organizationKeys.members(organizationId ?? "", false),
    queryFn: () => listOrganizationMembers(organizationId!),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
    retry: false,
  });

  let people: UserSummary[] = [];
  if (workspaceId) {
    people = (workspace.data?.items ?? [])
      .filter((member) => member.role === "ADMIN" && member.status === "ACTIVE")
      // Stored grants first: those are the people who run this workspace day to day.
      .sort((a, b) => Number(a.is_derived) - Number(b.is_derived))
      .map((member) => member.user);
  } else if (organizationId && scope && "roles" in scope) {
    const order = scope.roles;
    people = (organization.data?.items ?? [])
      .filter((member) => order.includes(member.role) && member.status === "ACTIVE")
      .sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role))
      .map((member) => member.user);
  }
  const names: string[] = [];
  for (const user of people) {
    if (user.id === selfId) {
      continue;
    }
    const name = nameOf(user);
    if (!names.includes(name)) {
      names.push(name);
    }
    if (names.length === MAX_NAMES) {
      break;
    }
  }
  return names;
};

/** "Ada" / "Ada or Grace" / "Ada, Grace or Linus". */
export const joinNames = (names: readonly string[]): string =>
  names.length <= 1 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
