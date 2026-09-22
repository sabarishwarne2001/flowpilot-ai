/**
 * HARDENING-T3:D21 — clause checks authored from the console.
 * Backend: /workspaces/{id}/assertions/clause-checks (app/api/v1/assertions.py).
 */
import { apiClient } from "@/services/api/client";
import type { AssertionDefinition } from "@/types/assertions";

export interface ClauseCheck {
  readonly rule_id: string;
  readonly name: string;
  readonly is_active: boolean;
  readonly node_key: string;
  readonly definition: AssertionDefinition | null;
}

const base = (workspaceId: string): string =>
  `/workspaces/${encodeURIComponent(workspaceId)}/assertions/clause-checks`;

export const listClauseChecks = async (workspaceId: string): Promise<ClauseCheck[]> =>
  (await apiClient.get<ClauseCheck[]>(base(workspaceId))).data;

export const createClauseCheck = async (workspaceId: string, name: string): Promise<ClauseCheck> =>
  (await apiClient.post<ClauseCheck>(base(workspaceId), { name })).data;

export const setClauseCheckActive = async (
  workspaceId: string,
  ruleId: string,
  isActive: boolean,
): Promise<ClauseCheck> =>
  (await apiClient.patch<ClauseCheck>(`${base(workspaceId)}/${encodeURIComponent(ruleId)}`, { is_active: isActive })).data;

export const deleteClauseCheck = async (workspaceId: string, ruleId: string): Promise<void> => {
  await apiClient.delete(`${base(workspaceId)}/${encodeURIComponent(ruleId)}`);
};
