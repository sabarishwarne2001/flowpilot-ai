/**
 * ARCH49-S2:process-api — Process Intelligence & the Governed Exception Agent.
 * Every route is gated on the process-intelligence capability (Enterprise): 402 without it.
 */
import { apiClient } from "@/services/api/client";
import type {
  AgentPolicy, AgentPolicyUpdate, ApproveOut, ConformanceOut, CostOut, DiscoveryOut, ObjectType, ProcessOverview,
  ProposalDetail, ProposalList, ProposalRow, RejectReason, SlaObjectType, SlaOut, SlaPolicy, SlaPolicyUpdate, SweepOut,
  TimelineOut,
} from "@/types/process";

const base = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}/process`;
const proposal = (workspaceId: string, id: string): string => `${base(workspaceId)}/agent/proposals/${encodeURIComponent(id)}`;

export interface ProposalFilters {
  readonly status?: string;
  readonly subject_id?: string;
  readonly subject_kind?: string;
  readonly limit?: number;
  readonly offset?: number;
}

export const processKeys = {
  all: (workspaceId: string) => ["process", workspaceId] as const,
  overview: (workspaceId: string) => ["process", workspaceId, "overview"] as const,
  discovery: (workspaceId: string, objectType: string, days: number) => ["process", workspaceId, "discovery", objectType, days] as const,
  timeline: (workspaceId: string, objectType: string, objectId: string) => ["process", workspaceId, "timeline", objectType, objectId] as const,
  conformance: (workspaceId: string, days: number) => ["process", workspaceId, "conformance", days] as const,
  sla: (workspaceId: string) => ["process", workspaceId, "sla"] as const,
  cost: (workspaceId: string, days: number) => ["process", workspaceId, "cost", days] as const,
  policy: (workspaceId: string) => ["process", workspaceId, "policy"] as const,
  proposals: (workspaceId: string) => ["process", workspaceId, "proposals"] as const,
  proposalList: (workspaceId: string, filters: ProposalFilters) => ["process", workspaceId, "proposals", "list", filters] as const,
  proposal: (workspaceId: string, id: string) => ["process", workspaceId, "proposals", "one", id] as const,
  forItem: (workspaceId: string, itemId: string) => ["process", workspaceId, "proposals", "item", itemId] as const,
};

const clean = (filters: ProposalFilters): Record<string, string | number> => {
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") {
      out[key] = value as string | number;
    }
  }
  return out;
};

// -- process intelligence ----------------------------------------------------------------------

export const getProcessOverview = async (workspaceId: string): Promise<ProcessOverview> =>
  (await apiClient.get<ProcessOverview>(`${base(workspaceId)}/overview`)).data;

export const getDiscovery = async (workspaceId: string, objectType: ObjectType, days: number): Promise<DiscoveryOut> =>
  (await apiClient.get<DiscoveryOut>(`${base(workspaceId)}/discovery`, { params: { object_type: objectType, days } })).data;

export const getObjectTimeline = async (workspaceId: string, objectType: ObjectType, objectId: string): Promise<TimelineOut> =>
  (await apiClient.get<TimelineOut>(`${base(workspaceId)}/objects/${encodeURIComponent(objectType)}/${encodeURIComponent(objectId)}`)).data;

export const getConformance = async (workspaceId: string, days: number): Promise<ConformanceOut> =>
  (await apiClient.get<ConformanceOut>(`${base(workspaceId)}/conformance`, { params: { days } })).data;

export const getSla = async (workspaceId: string): Promise<SlaOut> =>
  (await apiClient.get<SlaOut>(`${base(workspaceId)}/sla`)).data;

export const setSlaPolicy = async (workspaceId: string, objectType: SlaObjectType, body: SlaPolicyUpdate): Promise<SlaPolicy> =>
  (await apiClient.put<SlaPolicy>(`${base(workspaceId)}/sla/${encodeURIComponent(objectType)}`, body)).data;

export const getCost = async (workspaceId: string, days: number): Promise<CostOut> =>
  (await apiClient.get<CostOut>(`${base(workspaceId)}/cost`, { params: { days } })).data;

export const sweepNow = async (workspaceId: string): Promise<SweepOut> =>
  (await apiClient.post<SweepOut>(`${base(workspaceId)}/sweep`, {}, { timeout: 180_000 })).data;

// -- the exception agent -----------------------------------------------------------------------

export const getAgentPolicy = async (workspaceId: string): Promise<AgentPolicy> =>
  (await apiClient.get<AgentPolicy>(`${base(workspaceId)}/agent/policy`)).data;

export const setAgentPolicy = async (workspaceId: string, body: AgentPolicyUpdate): Promise<AgentPolicy> =>
  (await apiClient.put<AgentPolicy>(`${base(workspaceId)}/agent/policy`, body)).data;

export const listProposals = async (workspaceId: string, filters: ProposalFilters): Promise<ProposalList> =>
  (await apiClient.get<ProposalList>(`${base(workspaceId)}/agent/proposals`, { params: clean(filters) })).data;

export const getProposal = async (workspaceId: string, id: string): Promise<ProposalDetail> =>
  (await apiClient.get<ProposalDetail>(proposal(workspaceId, id))).data;

export const approveProposal = async (workspaceId: string, id: string): Promise<ApproveOut> =>
  (await apiClient.post<ApproveOut>(`${proposal(workspaceId, id)}/approve`, {})).data;

export const rejectProposal = async (workspaceId: string, id: string, reason: RejectReason): Promise<ProposalRow> =>
  (await apiClient.post<ProposalRow>(`${proposal(workspaceId, id)}/reject`, { reason })).data;

export const undoProposal = async (workspaceId: string, id: string): Promise<ProposalRow> =>
  (await apiClient.post<ProposalRow>(`${proposal(workspaceId, id)}/undo`, {})).data;
