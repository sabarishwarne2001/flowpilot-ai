/**
 * ARCH50-S2:api-sovereign — the organization's egress lockdown (capability-gated: 402 without Enterprise) and the
 * operator's Sovereign and RevOps consoles (superadmin: 404 for anyone else).
 */
import { apiClient } from "@/services/api/client";
import type {
  EgressDecision, EgressPolicy, EgressRefusalList, EgressRule, EgressRuleInput, LicenceStatus, LocalModelHealth,
  ReleaseStatus, SovereignStatus,
} from "@/types/sovereign";

const org = (organizationId: string): string => `/organizations/${encodeURIComponent(organizationId)}/egress`;

export const egressKeys = {
  all: (organizationId: string) => ["egress", organizationId] as const,
  policy: (organizationId: string) => ["egress", organizationId, "policy"] as const,
  refusals: (organizationId: string, days: number) => ["egress", organizationId, "refusals", days] as const,
};

export const sovereignKeys = {
  all: () => ["sovereign"] as const,
  status: () => ["sovereign", "status"] as const,
  refusals: (days: number) => ["sovereign", "refusals", days] as const,
  localModel: () => ["sovereign", "local-llm"] as const,
  release: () => ["sovereign", "release"] as const,
};

export const getEgressPolicy = async (organizationId: string): Promise<EgressPolicy> =>
  (await apiClient.get<EgressPolicy>(org(organizationId))).data;

export const setEgressLockdown = async (organizationId: string, lockdownEnabled: boolean): Promise<EgressPolicy> =>
  (await apiClient.put<EgressPolicy>(org(organizationId), { lockdown_enabled: lockdownEnabled })).data;

export const addEgressRule = async (organizationId: string, rule: EgressRuleInput): Promise<EgressRule> =>
  (await apiClient.post<EgressRule>(`${org(organizationId)}/rules`, rule)).data;

export const deleteEgressRule = async (organizationId: string, ruleId: string): Promise<void> => {
  await apiClient.delete(`${org(organizationId)}/rules/${encodeURIComponent(ruleId)}`);
};

export const getEgressRefusals = async (organizationId: string, days: number): Promise<EgressRefusalList> =>
  (await apiClient.get<EgressRefusalList>(`${org(organizationId)}/refusals`, { params: { days } })).data;

export const testEgressDestination = async (organizationId: string, channel: string,
                                            destination: string): Promise<EgressDecision> =>
  (await apiClient.post<EgressDecision>(`${org(organizationId)}/test`, { channel, destination })).data;

export const getSovereignStatus = async (): Promise<SovereignStatus> =>
  (await apiClient.get<SovereignStatus>("/admin/sovereign")).data;

export const getLocalModelHealth = async (): Promise<LocalModelHealth> =>
  (await apiClient.get<LocalModelHealth>("/admin/sovereign/local-llm")).data;

export const getAllRefusals = async (days: number): Promise<EgressRefusalList> =>
  (await apiClient.get<EgressRefusalList>("/admin/sovereign/refusals", { params: { days } })).data;

export const operatorEgressTest = async (channel: string, destination: string,
                                         organizationId?: string | undefined): Promise<EgressDecision> =>
  (await apiClient.post<EgressDecision>("/admin/sovereign/egress-test", {
    channel, destination, ...(organizationId ? { organization_id: organizationId } : {}),
  })).data;

export const installLicence = async (licence: string): Promise<LicenceStatus> =>
  (await apiClient.post<LicenceStatus>("/admin/sovereign/licence", { licence })).data;

export const getReleaseStatus = async (): Promise<ReleaseStatus> =>
  (await apiClient.get<ReleaseStatus>("/admin/sovereign/release")).data;
