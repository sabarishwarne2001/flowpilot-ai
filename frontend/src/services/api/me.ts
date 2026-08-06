/**
 * Actor-scoped API service for FlowPilot AI.
 *
 * Answers "who am I and what can I reach" without naming a tenant.
 *
 * getMeContext is the application bootstrap and the fix for a specific defect.
 * The pre-ARCH-01 OnboardingGuard called getWorkspace() and treated any falsy
 * result as "no workspace", so an expired token and a genuinely
 * membership-less user produced the same signal — and session expiry sent
 * people to "Create My Workspace" instead of the login page. Removal from a
 * workspace did the same, which is how removed members ended up founding
 * phantom organizations.
 *
 * The states are now distinguishable at the transport layer:
 *
 *   throws ApiError(401, UNAUTHORIZED)  -> session gone       -> /login
 *   resolves, requires_onboarding true  -> no tenant          -> onboarding
 *   resolves, organizations populated   -> normal             -> workspace
 *
 * A rejected promise and a resolved one can no longer be confused, which is
 * exactly what the old boolean check could not achieve.
 */

import apiClient from "@/services/api/client";
import { ME_ENDPOINTS } from "@/services/api/endpoints";

import type {
  MeContext,
  Organization,
  WorkspaceSummary,
} from "@/types/tenancy";

/**
 * Returns the complete bootstrap context in one round trip.
 *
 * Rejects with ApiError(401) when the session is invalid. Never resolves with
 * an "unauthenticated" shape — that distinction is the point of the endpoint.
 */
export const getMeContext = async (): Promise<MeContext> => {
  const response = await apiClient.get<MeContext>(ME_ENDPOINTS.context, {
    headers: { Accept: "application/json" },
  });
  return response.data;
};

/**
 * Returns every organization the actor actively belongs to.
 *
 * Multiple results are ordinary. The pre-ARCH-01 backend could not represent
 * this at all: a second membership raised MultipleResultsFound and returned
 * HTTP 500 on every subsequent request.
 */
export const getMyOrganizations = async (): Promise<Organization[]> => {
  const response = await apiClient.get<Organization[]>(
    ME_ENDPOINTS.organizations,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Returns every workspace the actor holds an EXPLICIT grant on.
 *
 * Deliberately excludes workspaces reachable only through organization-level
 * derived elevation. An organization admin of a large tenant would otherwise
 * receive every workspace in it — correct, but not what a personal workspace
 * list means. Use getMeContext for the complete, grouped view.
 */
export const getMyWorkspaces = async (): Promise<WorkspaceSummary[]> => {
  const response = await apiClient.get<WorkspaceSummary[]>(
    ME_ENDPOINTS.workspaces,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const meApi = {
  getMeContext,
  getMyOrganizations,
  getMyWorkspaces,
};

export default meApi;
