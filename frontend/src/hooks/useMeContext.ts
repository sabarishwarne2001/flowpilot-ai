/**
 * Bootstrap context query for FlowPilot AI.
 *
 * Wraps GET /me/context, the single call that reports who the actor is, which
 * tenants they belong to, and where they should land.
 *
 * Every consumer shares one query key, so the context is fetched once per
 * session and every guard, switcher, and page reads the same answer. A second
 * fetch path would be a second opportunity to disagree about which tenant the
 * user is in.
 */

import { useQuery } from "@tanstack/react-query";

import { getMeContext } from "@/services/api/me";
import { ApiError } from "@/services/api/client";
import { API_ERROR_CODES } from "@/constants/errorCodes";
import { useAuthStore } from "@/store/useAuthStore";

import type { MeContext } from "@/types/tenancy";

/**
 * Query key factory.
 *
 * Scoped by user id so a sign-out followed by a different sign-in cannot serve
 * the previous actor's tenants from cache during the moment before the new
 * fetch resolves.
 */
export const meContextQueryKey = (userId: string | null | undefined) =>
  ["me", "context", userId ?? "anonymous"] as const;

export interface UseMeContextResult {
  context: MeContext | undefined;
  isLoading: boolean;
  isFetching: boolean;
  /** True when the server rejected the session. Route to login, not onboarding. */
  isUnauthorized: boolean;
  error: unknown;
  refetch: () => void;
}

/**
 * Fetches the bootstrap context for the authenticated actor.
 *
 * Disabled without a session, so an anonymous visitor never issues a request
 * that is certain to 401.
 *
 * isUnauthorized is surfaced separately rather than left for callers to derive
 * from the error. Deriving it means every caller re-implements the check, and
 * one caller getting it wrong reintroduces exactly the defect this step
 * removes: treating a rejected session as "this user has no workspace".
 */
export const useMeContext = (): UseMeContextResult => {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const userId = useAuthStore((state) => state.user?.id ?? null);

  const query = useQuery({
    queryKey: meContextQueryKey(userId),
    queryFn: getMeContext,
    enabled: isAuthenticated,

    // The context changes only when tenancy changes — a switch, an invitation
    // accepted, a role change. Those paths invalidate explicitly, so polling
    // would be waste.
    staleTime: 1000 * 60 * 5,

    // Overrides the global refetchOnMount: "always", which defeats staleTime.
    // Several guards mount this hook on a single navigation, so "always" turns
    // one page load into three or four identical bootstrap requests. Tenancy
    // changes are explicit — a switch, an accepted invitation, a role change —
    // and each invalidates the key directly.
    refetchOnMount: false,

    // A 401 is final. Retrying only delays the redirect to login.
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 401) {
        return false;
      }
      return failureCount < 2;
    },
  });

  const isUnauthorized =
    query.error instanceof ApiError &&
    (query.error.status === 401 ||
      query.error.code === API_ERROR_CODES.UNAUTHORIZED);

  return {
    context: query.data,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isUnauthorized,
    error: isUnauthorized ? undefined : query.error,
    refetch: () => {
      void query.refetch();
    },
  };
};
